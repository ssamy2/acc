import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from backend.services.delivery_service import get_delivery_service
from backend.core_engine.logger import get_logger, log_request, log_response

logger = get_logger("DeliveryAPI")

router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])


class PhoneRequest(BaseModel):
    phone_number: str


class ConfirmRequest(BaseModel):
    phone_number: str


@router.post("/check-session")
async def check_session(request: PhoneRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/delivery/check-session", {"phone": phone})
    
    try:
        service = get_delivery_service()
        result = await service.check_session_availability(phone)
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/request-code")
async def request_code(request: PhoneRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/delivery/request-code", {"phone": phone})
    
    try:
        service = get_delivery_service()
        result = await service.request_code_ready(phone)
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in request code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get-code")
async def get_code(request: PhoneRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/delivery/get-code", {"phone": phone})
    
    try:
        service = get_delivery_service()
        result = await service.get_received_code(phone)
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        return result
        
    except Exception as e:
        logger.error(f"Error getting code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm")
async def confirm_delivery(request: ConfirmRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/delivery/confirm", {"phone": phone})
    
    try:
        service = get_delivery_service()
        result = await service.confirm_delivery(phone)
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming delivery: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/force-secure")
async def force_secure(request: PhoneRequest, req: Request):
    start_time = time.time()
    phone = request.phone_number
    log_request(logger, "POST", "/delivery/force-secure", {"phone": phone})
    
    try:
        service = get_delivery_service()
        result = await service.force_secure_account(phone, "manual_trigger")
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error force securing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts")
async def get_all_accounts(req: Request):
    start_time = time.time()
    log_request(logger, "GET", "/delivery/accounts", None)
    
    try:
        service = get_delivery_service()
        accounts = await service.get_all_accounts()
        
        duration = time.time() - start_time
        return {
            "status": "success",
            "accounts": accounts,
            "count": len(accounts),
            "duration": duration
        }
        
    except Exception as e:
        logger.error(f"Error getting accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def get_logs(phone: Optional[str] = None, limit: int = 100, req: Request = None):
    start_time = time.time()
    log_request(logger, "GET", "/delivery/logs", {"phone": phone, "limit": limit})
    
    try:
        service = get_delivery_service()
        logs = await service.get_security_logs(phone, limit)
        
        duration = time.time() - start_time
        return {
            "status": "success",
            "logs": logs,
            "count": len(logs),
            "duration": duration
        }
        
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/account/{phone}")
async def delete_account(phone: str, req: Request):
    start_time = time.time()
    log_request(logger, "DELETE", f"/delivery/account/{phone}", None)
    
    try:
        service = get_delivery_service()
        result = await service.delete_account(phone)
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["error"])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting account: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/incomplete-sessions")
async def get_incomplete_sessions(req: Request):
    start_time = time.time()
    log_request(logger, "GET", "/delivery/incomplete-sessions", None)
    
    try:
        service = get_delivery_service()
        sessions = await service.get_incomplete_sessions_list()
        
        duration = time.time() - start_time
        return {
            "status": "success",
            "sessions": sessions,
            "count": len(sessions),
            "duration": duration
        }
        
    except Exception as e:
        logger.error(f"Error getting incomplete sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup-expired")
async def cleanup_expired(req: Request):
    start_time = time.time()
    log_request(logger, "POST", "/delivery/cleanup-expired", None)
    
    try:
        service = get_delivery_service()
        result = await service.cleanup_expired()
        
        duration = time.time() - start_time
        result["duration"] = duration
        log_response(logger, 200, result)
        
        return result
        
    except Exception as e:
        logger.error(f"Error cleaning up expired sessions: {e}")
        raise HTTPException(status_code=500, detail=str(e))
