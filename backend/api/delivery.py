"""
Delivery API Routes
Handles: request delivery code, confirm delivery
"""

import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core_engine.logger import get_logger
from backend.core_engine.pyrogram_client import get_session_manager
from backend.core_engine.telethon_client import get_telethon_manager
from backend.core_engine.credentials_logger import log_credentials
from backend.models.database import (
    DeliveryStatus, get_account, update_account, log_auth_action
)

logger = get_logger("DeliveryAPI")
router = APIRouter(tags=["Delivery"])

from config import API_ID, API_HASH


class DeliveryConfirmRequest(BaseModel):
    received: bool = True


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")

def get_telethon():
    return get_telethon_manager(API_ID, API_HASH)


@router.post("/delivery/request-code/{account_id}")
async def request_delivery_code(account_id: str):
    """Request login code for delivery to buyer"""
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if account.delivery_status == DeliveryStatus.BUYER_DELIVERED:
        raise HTTPException(status_code=400, detail="Account already delivered")
    
    if not account.pyrogram_session:
        raise HTTPException(status_code=400, detail="No session available")
    
    manager = get_pyrogram()
    
    try:
        connected = await manager.connect_from_string(phone, account.pyrogram_session)
        if not connected:
            raise HTTPException(status_code=400, detail="Failed to connect session - session may be expired")
    except Exception as e:
        logger.error(f"Failed to connect for delivery: {e}")
        raise HTTPException(status_code=400, detail=f"Session connection failed: {str(e)}")
    
    # Send login code to buyer
    try:
        code_result = await manager.send_code(phone)
        if code_result.get("status") not in ["code_sent", "already_logged_in"]:
            raise HTTPException(status_code=400, detail="Failed to send code")
    except Exception as e:
        logger.error(f"Failed to send code: {e}")
        raise HTTPException(status_code=400, detail="Failed to send code")
    
    await update_account(
        phone,
        delivery_status=DeliveryStatus.CODE_SENT,
        code_sent_at=datetime.utcnow(),
        confirmation_deadline=datetime.utcnow() + timedelta(minutes=5)
    )
    
    await log_auth_action(phone, "delivery_request", "success")
    
    return {
        "status": "success",
        "message": "Login code sent to buyer",
        "account_id": phone,
        "has_2fa_password": bool(account.generated_password),
        "has_2fa": account.has_2fa,
        "deadline_minutes": 5
    }


@router.post("/delivery/confirm/{account_id}")
async def confirm_delivery(account_id: str, request: DeliveryConfirmRequest):
    """Confirm delivery received by buyer"""
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not request.received:
        return {"status": "cancelled", "message": "Delivery cancelled"}
    
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    
    # CRITICAL: Log out sessions BEFORE clearing from DB
    try:
        if account.pyrogram_session:
            connected = await manager.connect_from_string(phone, account.pyrogram_session)
            if connected:
                await manager.log_out(phone)
                logger.info(f"Pyrogram logged out for delivery: {phone}")
            else:
                await manager.disconnect(phone)
        else:
            await manager.disconnect(phone)
    except Exception as e:
        logger.warning(f"Pyrogram logout failed: {e}")
        try: await manager.disconnect(phone)
        except: pass
    
    try:
        if account.telethon_session:
            connected = await telethon_mgr.connect_from_string(phone, account.telethon_session)
            if connected:
                await telethon_mgr.log_out(phone)
                logger.info(f"Telethon logged out for delivery: {phone}")
            else:
                await telethon_mgr.disconnect(phone)
    except Exception as e:
        logger.warning(f"Telethon logout failed: {e}")
        try: await telethon_mgr.disconnect(phone)
        except: pass
    
    new_count = (account.delivery_count or 0) + 1
    
    await update_account(
        phone,
        delivery_status=DeliveryStatus.BUYER_DELIVERED,
        delivered_at=datetime.utcnow(),
        delivery_count=new_count,
        pyrogram_session=None,
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
