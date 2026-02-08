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
    """
    Step 2: Buyer has requested a login code from Telegram app.
    Connect Pyrogram (to read code from 777000 later). Do NOT send any code.
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if account.delivery_status == DeliveryStatus.BUYER_DELIVERED:
        raise HTTPException(status_code=400, detail="Account already delivered")
    
    # SECURITY: Only allow delivery for bot_only accounts
    transfer_mode = account.transfer_mode.value if account.transfer_mode else "bot_only"
    if transfer_mode != "bot_only":
        raise HTTPException(
            status_code=400,
            detail=f"Delivery blocked: account is in '{transfer_mode}' mode. Transition to bot_only first."
        )
    
    if not account.pyrogram_session:
        raise HTTPException(status_code=400, detail="No session available")
    
    manager = get_pyrogram()
    
    try:
        connected = await manager.connect_from_string(phone, account.pyrogram_session)
        if not connected:
            raise HTTPException(status_code=400, detail="Failed to connect session - session may be expired")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to connect for delivery: {e}")
        raise HTTPException(status_code=400, detail=f"Session connection failed: {str(e)}")
    
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
    Step 3: Read login code from Telegram messages (777000).
    Returns code + 2FA password. Disconnects Pyrogram after reading.
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    manager = get_pyrogram()
    
    if phone not in manager.active_clients:
        if account.pyrogram_session:
            try:
                await manager.connect_from_string(phone, account.pyrogram_session)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Reconnect failed: {e}")
        else:
            raise HTTPException(status_code=400, detail="No session available")
    
    code = await manager.get_last_telegram_code(phone, max_age_seconds=300)
    
    if not code:
        return {"status": "waiting", "message": "No code received yet."}
    
    await update_account(phone, delivery_status=DeliveryStatus.CODE_SENT, last_code=code)
    await log_auth_action(phone, "delivery_code_read", "success", f"Code: {code[:2]}***")
    
    # Disconnect Pyrogram after reading (free RAM)
    await manager.disconnect(phone)
    
    return {
        "status": "success",
        "code": code,
        "has_password": bool(account.generated_password),
        "password": account.generated_password,
        "timeout_minutes": 30
    }


@router.post("/delivery/confirm/{account_id}")
async def confirm_delivery(account_id: str, request: DeliveryConfirmRequest):
    """
    Step 4: Confirm delivery. Sequential connection management.
    Pyrogram connect → logout → disconnect, then Telethon connect → logout → disconnect.
    """
    phone = account_id
    account = await get_account(phone)
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not request.received:
        return {"status": "cancelled", "message": "Delivery cancelled"}
    
    manager = get_pyrogram()
    telethon_mgr = get_telethon()
    logout_results = []
    
    # Pyrogram logout (sequential)
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
        logger.warning(f"Pyrogram logout failed: {e}")
        logout_results.append(f"Pyrogram: error")
        try:
            await manager.disconnect(phone)
        except:
            pass
    
    # Telethon logout (sequential - after Pyrogram is disconnected)
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
        logger.warning(f"Telethon logout failed: {e}")
        logout_results.append(f"Telethon: error")
        try:
            await telethon_mgr.disconnect(phone)
        except:
            pass
    
    new_count = (account.delivery_count or 0) + 1
    
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
    
    log_credentials(
        phone=phone,
        action="DELIVERY_CONFIRMED",
        telegram_id=account.telegram_id,
        extra_data={"delivery_number": new_count, "logout_results": logout_results}
    )
    
    await log_auth_action(phone, "delivery_confirm", "success", f"Delivery #{new_count}")
    
    return {
        "status": "success",
        "message": f"Delivery #{new_count} confirmed",
        "account_id": phone,
        "delivery_number": new_count,
        "logout_results": logout_results
    }
