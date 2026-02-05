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


def extract_telegram_code(text: str) -> Optional[str]:
    """Extract 5-6 digit Telegram verification code from text"""
    patterns = [
        r'(?:code|verification\s+code|login\s+code|код|رمز|كود|código)[\s:：]*[^\d]*?(\d{5,6})',
        r'(\d{5,6})[\s\-–—]*(?:is your|هو رمز|это ваш)',
        r'(?:your|tu|votre)[\s\w]*(?:code|код|رمز)[\s:：]*(\d{5,6})',
        r'(?:^|[\s\n\r.!?:：])\s*(\d{5,6})(?=[\s\n\r.!?:：]|$)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.UNICODE | re.MULTILINE)
        if match:
            code = match.group(1)
            if len(code) in [5, 6] and code not in ['00000', '000000']:
                return code
    return None


def extract_code_from_email(subject: str, body: str) -> Optional[str]:
    """Extract code from both subject and body - checks subject first"""
    if subject:
        code = extract_telegram_code(subject)
        if code:
            return code
    if body:
        return extract_telegram_code(body)
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
        "to": "email-for-<encrypted_hash>@channelsseller.site",
        "hash": "<encrypted_hash>",
        "subject": "Telegram Login Code",
        "body": "Your verification code is 12345..."
    }
    """
    try:
        # Parse JSON
        try:
            data = await request.json()
        except Exception as e:
            raw_body = await request.body()
            raw_body_str = raw_body.decode('utf-8', errors='ignore')
            logger.error(f"Failed to parse JSON: {e}")
            return {"status": "error", "message": "Invalid JSON"}
        
        # Extract fields
        from_email = data.get("from", data.get("from_email", "unknown"))
        to_email = data.get("to", "")
        email_hash = data.get("hash", "")
        subject = data.get("subject", "")
        body = data.get("body", "")
        
        # If hash not provided, extract from email address
        if not email_hash:
            email_hash = extract_hash_from_email(to_email)
        
        # Normalize hash to lowercase for consistent storage
        if email_hash:
            email_hash = email_hash.lower()
        
        # Extract verification code from subject and body
        code = extract_code_from_email(subject, body)
        
        if code:
            logger.info(f"✅ Code {code} extracted for hash: {email_hash}")
            
            # Store the code
            received_codes[email_hash] = {
                "code": code,
                "received_at": datetime.now(),
                "from": from_email,
                "to": to_email,
                "subject": subject,
                "raw_body": body
            }
            
            return {
                "status": "success",
                "message": "Code extracted and stored",
                "hash": email_hash,
                "code_extracted": True
            }
        else:
            logger.warning(f"⚠️ No code found for hash: {email_hash}")
            
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
