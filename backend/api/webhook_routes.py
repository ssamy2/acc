"""
Email Webhook API Routes
Receives email notifications from Cloudflare Email Worker
Endpoint: /api3/webhook
"""

import re
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from backend.core_engine.logger import get_logger

logger = get_logger("EmailWebhook")

router = APIRouter(prefix="/api3", tags=["Email Webhook"])

# In-memory storage for received email codes
# Format: {email_hash: {"code": "12345", "received_at": datetime, "raw_body": "..."}}
email_codes_store: Dict[str, Dict[str, Any]] = {}

# Alias for backward compatibility
received_codes = email_codes_store


def get_code_by_hash(email_hash: str) -> Optional[str]:
    """
    Get verification code by email hash (non-async helper function)
    Returns the code if found, None otherwise
    """
    if email_hash in email_codes_store:
        data = email_codes_store[email_hash]
        return data.get("code")
    return None


def store_code(email_hash: str, code: str, extra_data: Dict[str, Any] = None):
    """Store a verification code for a hash"""
    email_codes_store[email_hash] = {
        "code": code,
        "received_at": datetime.now(),
        **(extra_data or {})
    }
    logger.info(f"💾 Code stored for hash: {email_hash}")


class EmailPayload(BaseModel):
    """Expected payload from Cloudflare Email Worker"""
    from_email: Optional[str] = None
    to: str
    hash: Optional[str] = None
    subject: Optional[str] = None
    body: str
    timestamp: Optional[str] = None


def extract_telegram_code(body: str) -> Optional[str]:
    """Extract 5-digit Telegram verification code from email body"""
    # Pattern for Telegram verification codes (5 digits)
    patterns = [
        r'(?:code|код|رمز|كود)[\s:]*(\d{5})',  # "code: 12345" or "код: 12345"
        r'(\d{5})(?:\s*is your|هو رمز)',  # "12345 is your code"
        r'\b(\d{5})\b',  # Any standalone 5-digit number
    ]
    
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None


def extract_hash_from_email(to_email: str) -> Optional[str]:
    """Extract hash from email address: email-for-<hash>@domain"""
    match = re.search(r'email-for-([^@]+)@', to_email)
    if match:
        return match.group(1)
    return None


@router.post("/webhook")
async def receive_email_webhook(request: Request):
    """
    Receive email notifications from Cloudflare Email Worker
    
    Expected JSON payload:
    {
        "from": "sender@telegram.org",
        "to": "email-for-S12345678@channelsseller.site",
        "hash": "S12345678",
        "subject": "Telegram Login Code",
        "body": "Your verification code is 12345..."
    }
    """
    try:
        # Get raw body for logging
        raw_body = await request.body()
        raw_body_str = raw_body.decode('utf-8', errors='ignore')
        
        # Log everything received
        logger.info("=" * 60)
        logger.info("📧 EMAIL WEBHOOK RECEIVED")
        logger.info("=" * 60)
        logger.info(f"Timestamp: {datetime.now().isoformat()}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Raw Body: {raw_body_str}")
        logger.info("=" * 60)
        
        # Parse JSON
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")
            logger.error(f"Raw body was: {raw_body_str}")
            return {"status": "error", "message": "Invalid JSON", "raw": raw_body_str[:500]}
        
        # Log parsed data
        logger.info("📋 PARSED DATA:")
        for key, value in data.items():
            logger.info(f"  {key}: {value}")
        
        # Extract fields
        from_email = data.get("from", data.get("from_email", "unknown"))
        to_email = data.get("to", "")
        email_hash = data.get("hash", "")
        subject = data.get("subject", "")
        body = data.get("body", "")
        
        # If hash not provided, extract from email address
        if not email_hash:
            email_hash = extract_hash_from_email(to_email)
        
        logger.info(f"📬 From: {from_email}")
        logger.info(f"📬 To: {to_email}")
        logger.info(f"📬 Hash: {email_hash}")
        logger.info(f"📬 Subject: {subject}")
        logger.info(f"📬 Body Preview: {body[:200]}...")
        
        # Extract verification code
        code = extract_telegram_code(body)
        
        if code:
            logger.info(f"✅ EXTRACTED CODE: {code}")
            
            # Store the code
            received_codes[email_hash] = {
                "code": code,
                "received_at": datetime.now(),
                "from": from_email,
                "to": to_email,
                "subject": subject,
                "raw_body": body
            }
            
            # Also store by phone-like hash
            if email_hash:
                received_codes[email_hash] = received_codes[email_hash]
            
            logger.info(f"💾 Code stored for hash: {email_hash}")
            
            return {
                "status": "success",
                "message": "Code extracted and stored",
                "hash": email_hash,
                "code_extracted": True
            }
        else:
            logger.warning(f"⚠️ No verification code found in email body")
            
            # Still store the raw data for debugging
            received_codes[email_hash] = {
                "code": None,
                "received_at": datetime.now(),
                "from": from_email,
                "to": to_email,
                "subject": subject,
                "raw_body": body
            }
            
            return {
                "status": "success",
                "message": "Email received but no code found",
                "hash": email_hash,
                "code_extracted": False
            }
            
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}


@router.get("/webhook/code/{email_hash}")
async def get_code_by_hash_endpoint(email_hash: str, timeout: int = 60):
    """
    Get verification code by email hash
    Waits up to 'timeout' seconds for the code to arrive
    """
    logger.info(f"🔍 Looking for code with hash: {email_hash}")
    
    start_time = datetime.now()
    
    while (datetime.now() - start_time).seconds < timeout:
        if email_hash in received_codes:
            data = received_codes[email_hash]
            if data.get("code"):
                logger.info(f"✅ Found code for {email_hash}: {data['code']}")
                return {
                    "status": "success",
                    "code": data["code"],
                    "received_at": data["received_at"].isoformat()
                }
        
        await asyncio.sleep(2)
    
    logger.warning(f"⏱️ Timeout waiting for code: {email_hash}")
    return {
        "status": "timeout",
        "message": f"No code received within {timeout} seconds",
        "hash": email_hash
    }


@router.get("/webhook/codes")
async def list_all_codes():
    """List all received codes (for debugging)"""
    result = {}
    for hash_key, data in received_codes.items():
        result[hash_key] = {
            "code": data.get("code"),
            "received_at": data["received_at"].isoformat() if data.get("received_at") else None,
            "from": data.get("from"),
            "to": data.get("to"),
            "subject": data.get("subject")
        }
    
    return {"status": "success", "codes": result, "count": len(result)}


@router.delete("/webhook/codes")
async def clear_all_codes():
    """Clear all stored codes"""
    received_codes.clear()
    logger.info("🗑️ All stored codes cleared")
    return {"status": "success", "message": "All codes cleared"}


@router.get("/webhook/health")
async def webhook_health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "stored_codes_count": len(received_codes)
    }
