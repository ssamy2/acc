"""
Account Audit & Health Check System
Comprehensive account verification with detailed reporting
"""

from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core_engine.logger import get_logger
from backend.core_engine.pyrogram_client import get_session_manager
from backend.core_engine.telethon_client import get_telethon_manager
from backend.models.database import (
    get_account, update_account, async_session, Account,
    AuthStatus, DeliveryStatus, TransferMode
)
from sqlalchemy import select

logger = get_logger("AuditAPI")
router = APIRouter(prefix="/audit", tags=["Audit"])

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"
OUR_EMAIL_DOMAIN = "channelsseller.site"


class AccountHealth(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    FROZEN = "frozen"


class IssueType(Enum):
    SESSION_DEAD = "session_dead"
    EMAIL_CHANGED = "email_changed"
    RECOVERY_EMAIL_CHANGED = "recovery_email_changed"
    EXTRA_SESSION = "extra_session"
    DELETE_REQUEST = "delete_request"
    NO_2FA = "no_2fa"
    NO_RECOVERY_EMAIL = "no_recovery_email"


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


def get_telethon():
    return get_telethon_manager(API_ID, API_HASH)


async def check_pyrogram_session(phone: str) -> Dict[str, Any]:
    """Check Pyrogram session health with real connection"""
    manager = get_pyrogram()
    try:
        me = await manager.get_me_info(phone)
        if me.get("status") == "success":
            return {"active": True, "user_id": me.get("id")}
    except:
        pass
    
    account = await get_account(phone)
    if account and account.pyrogram_session:
        try:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
            if connected:
                me = await manager.get_me_info(phone)
                if me.get("status") == "success":
                    return {"active": True, "user_id": me.get("id"), "reconnected": True}
        except:
            pass
    
    return {"active": False, "error": "Session dead or unauthorized"}


async def check_telethon_session(phone: str) -> Dict[str, Any]:
    """Check Telethon session health with real connection"""
    manager = get_telethon()
    try:
        client = manager.active_clients.get(phone)
        if client and await client.is_user_authorized():
            me = await client.get_me()
            return {"active": True, "user_id": me.id}
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
                    return {"active": True, "user_id": me.id, "reconnected": True}
        except:
            pass
    
    return {"active": False, "error": "Session dead or unauthorized"}


async def get_account_emails_live(phone: str) -> Dict[str, Any]:
    """
    Get BOTH login email AND recovery email from Telegram
    login_email_pattern = Email used for login (alternative to phone)
    email_unconfirmed_pattern = Pending email change
    has_recovery = Recovery email for 2FA password reset
    """
    manager = get_pyrogram()
    
    try:
        security = await manager.get_security_info(phone)
        if security.get("status") != "success":
            return {"status": "error", "error": security.get("error")}
        
        result = {
            "status": "success",
            "has_2fa": security.get("has_password", False),
            "login_email": security.get("login_email_pattern"),
            "login_email_status": "confirmed" if security.get("login_email_pattern") else "none",
            "pending_email": security.get("email_unconfirmed_pattern"),
            "has_recovery_email": security.get("has_recovery_email", False),
            "recovery_email_pattern": None,
            "password_hint": security.get("password_hint"),
            "sessions_count": security.get("other_sessions_count", 0),
            "current_session": security.get("current_session"),
            "other_sessions": security.get("other_sessions", [])
        }
        
        if security.get("email_unconfirmed_pattern"):
            result["login_email_status"] = "pending_confirmation"
            result["pending_email"] = security["email_unconfirmed_pattern"]
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting emails for {phone}: {e}")
        return {"status": "error", "error": str(e)}


async def check_delete_request(phone: str) -> bool:
    """Check if account has pending delete request"""
    manager = get_pyrogram()
    try:
        client = manager.active_clients.get(phone)
        if client:
            # TODO: Check for account.DeleteAccount request via raw API
            pass
    except:
        pass
    return False


async def audit_single_account(phone: str) -> Dict[str, Any]:
    """Full audit of a single account"""
    account = await get_account(phone)
    if not account:
        return {"status": "error", "error": "Account not found"}
    
    issues = []
    health = AccountHealth.HEALTHY
    
    pyrogram_health = await check_pyrogram_session(phone)
    telethon_health = await check_telethon_session(phone)
    emails = await get_account_emails_live(phone)
    delete_request = await check_delete_request(phone)
    
    is_our_login_email = False
    is_our_recovery_email = False
    
    if emails.get("login_email"):
        is_our_login_email = OUR_EMAIL_DOMAIN in str(emails["login_email"])
    if emails.get("pending_email"):
        is_our_login_email = OUR_EMAIL_DOMAIN in str(emails["pending_email"])
    
    mode = account.transfer_mode or TransferMode.BOT_ONLY
    expected_sessions = 1 if mode == TransferMode.BOT_ONLY else 2
    
    if not pyrogram_health["active"]:
        issues.append({
            "type": IssueType.SESSION_DEAD.value,
            "severity": "critical",
            "message": "Pyrogram session is dead",
            "details": pyrogram_health.get("error")
        })
        health = AccountHealth.CRITICAL
    
    if not telethon_health["active"]:
        issues.append({
            "type": IssueType.SESSION_DEAD.value,
            "severity": "critical",
            "message": "Telethon session is dead",
            "details": telethon_health.get("error")
        })
        health = AccountHealth.CRITICAL
    
    if emails.get("login_email") and not is_our_login_email:
        issues.append({
            "type": IssueType.EMAIL_CHANGED.value,
            "severity": "critical",
            "message": "Login email changed to unknown address",
            "current": emails["login_email"]
        })
        health = AccountHealth.FROZEN
    
    if emails.get("has_2fa") and not emails.get("has_recovery_email"):
        issues.append({
            "type": IssueType.NO_RECOVERY_EMAIL.value,
            "severity": "warning",
            "message": "2FA enabled but no recovery email set"
        })
        if health == AccountHealth.HEALTHY:
            health = AccountHealth.WARNING
    
    sessions_count = emails.get("sessions_count", 0) + 1
    if sessions_count > expected_sessions:
        extra = sessions_count - expected_sessions
        issues.append({
            "type": IssueType.EXTRA_SESSION.value,
            "severity": "warning" if mode == TransferMode.USER_KEEPS_SESSION else "critical",
            "message": f"{extra} unexpected session(s) detected",
            "expected": expected_sessions,
            "actual": sessions_count,
            "sessions": emails.get("other_sessions", [])
        })
        if mode == TransferMode.BOT_ONLY:
            health = AccountHealth.FROZEN
    
    if delete_request:
        issues.append({
            "type": IssueType.DELETE_REQUEST.value,
            "severity": "critical",
            "message": "Account has pending delete request"
        })
        health = AccountHealth.FROZEN
    
    if not emails.get("has_2fa"):
        issues.append({
            "type": IssueType.NO_2FA.value,
            "severity": "warning",
            "message": "2FA not enabled"
        })
    
    await update_account(
        phone,
        pyrogram_healthy=pyrogram_health["active"],
        telethon_healthy=telethon_health["active"],
        has_delete_request=delete_request,
        last_session_check=datetime.utcnow()
    )
    
    return {
        "phone": phone,
        "telegram_id": account.telegram_id,
        "health": health.value,
        "transfer_mode": mode.value,
        "sessions": {
            "pyrogram": pyrogram_health,
            "telethon": telethon_health,
            "total_count": sessions_count,
            "expected": expected_sessions
        },
        "emails": {
            "login_email": emails.get("login_email"),
            "login_email_status": emails.get("login_email_status"),
            "pending_email": emails.get("pending_email"),
            "has_recovery_email": emails.get("has_recovery_email"),
            "is_our_email": is_our_login_email
        },
        "security": {
            "has_2fa": emails.get("has_2fa", False),
            "password_hint": emails.get("password_hint"),
            "has_delete_request": delete_request
        },
        "issues": issues,
        "issues_count": len(issues),
        "target_email": account.target_email,
        "audited_at": datetime.utcnow().isoformat()
    }


@router.get("/account/{account_id}")
async def audit_account(account_id: str):
    """Full audit of single account"""
    return await audit_single_account(account_id)


@router.get("/report")
async def get_audit_report():
    """
    Generate comprehensive report of all accounts
    Categories: healthy, warning, critical, frozen
    """
    async with async_session() as session:
        result = await session.execute(select(Account))
        accounts = result.scalars().all()
    
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_accounts": len(accounts),
        "summary": {
            "healthy": 0,
            "warning": 0,
            "critical": 0,
            "frozen": 0
        },
        "healthy_accounts": [],
        "warning_accounts": [],
        "critical_accounts": [],
        "frozen_accounts": [],
        "issues_breakdown": {}
    }
    
    for acc in accounts:
        if acc.delivery_status == DeliveryStatus.BUYER_DELIVERED:
            continue
        
        try:
            audit = await audit_single_account(acc.phone)
            health = audit.get("health", "critical")
            
            account_summary = {
                "phone": acc.phone,
                "telegram_id": acc.telegram_id,
                "health": health,
                "issues_count": audit.get("issues_count", 0),
                "issues": audit.get("issues", []),
                "sessions": audit.get("sessions"),
                "emails": audit.get("emails")
            }
            
            if health == "healthy":
                report["summary"]["healthy"] += 1
                report["healthy_accounts"].append(account_summary)
            elif health == "warning":
                report["summary"]["warning"] += 1
                report["warning_accounts"].append(account_summary)
            elif health == "critical":
                report["summary"]["critical"] += 1
                report["critical_accounts"].append(account_summary)
            elif health == "frozen":
                report["summary"]["frozen"] += 1
                report["frozen_accounts"].append(account_summary)
            
            for issue in audit.get("issues", []):
                issue_type = issue.get("type", "unknown")
                if issue_type not in report["issues_breakdown"]:
                    report["issues_breakdown"][issue_type] = []
                report["issues_breakdown"][issue_type].append({
                    "phone": acc.phone,
                    "message": issue.get("message"),
                    "severity": issue.get("severity")
                })
                
        except Exception as e:
            logger.error(f"Error auditing {acc.phone}: {e}")
            report["summary"]["critical"] += 1
            report["critical_accounts"].append({
                "phone": acc.phone,
                "error": str(e)
            })
    
    return report


@router.post("/freeze/{account_id}")
async def freeze_account(account_id: str, reason: str = "manual"):
    """Move account to frozen/backup database"""
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # TODO: Move to backup database
    await update_account(
        phone,
        delivery_status=DeliveryStatus.FORCE_SECURED,
        audit_issues=f"Frozen: {reason}"
    )
    
    return {
        "status": "frozen",
        "phone": phone,
        "reason": reason,
        "message": "Account frozen. User can only retrieve 2FA codes for their ID."
    }


@router.post("/terminate-extra-sessions/{account_id}")
async def terminate_extra_sessions(account_id: str):
    """Terminate extra sessions based on transfer mode"""
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    
    mode = account.transfer_mode or TransferMode.BOT_ONLY
    emails = await get_account_emails_live(phone)
    
    other_sessions = emails.get("other_sessions", [])
    terminated = 0
    
    if mode == TransferMode.BOT_ONLY:
        for sess in other_sessions:
            try:
                await manager.terminate_session(phone, sess.get("hash"))
                terminated += 1
            except Exception as e:
                logger.warning(f"Failed to terminate session: {e}")
    
    elif mode == TransferMode.USER_KEEPS_SESSION:
        if len(other_sessions) > 1:
            for sess in other_sessions[1:]:
                try:
                    await manager.terminate_session(phone, sess.get("hash"))
                    terminated += 1
                except Exception as e:
                    logger.warning(f"Failed to terminate session: {e}")
    
    return {
        "status": "success",
        "terminated": terminated,
        "remaining": len(other_sessions) - terminated + 1
    }
