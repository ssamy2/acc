import asyncio
import os
import re
import time
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError
)
from telethon.tl.functions.account import GetAuthorizationsRequest
from backend.core_engine.logger import get_logger

logger = get_logger("TelethonClient")


class TelethonSessionManager:
    
    def __init__(self, api_id: int, api_hash: str, sessions_dir: str = "sessions"):
        self.api_id = api_id
        self.api_hash = api_hash
        
        # Use absolute path for sessions directory
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.sessions_dir = os.path.join(base_dir, sessions_dir)
        
        self.active_clients: Dict[str, TelegramClient] = {}
        self.phone_code_hashes: Dict[str, str] = {}
        # Per-phone locks for concurrency isolation
        self._locks: Dict[str, asyncio.Lock] = {}
        
        os.makedirs(self.sessions_dir, exist_ok=True)
        logger.info(f"TelethonSessionManager initialized. Sessions dir: {self.sessions_dir}")
    
    def _get_lock(self, phone: str) -> asyncio.Lock:
        if phone not in self._locks:
            self._locks[phone] = asyncio.Lock()
        return self._locks[phone]
    
    def _get_session_path(self, phone: str) -> str:
        safe_phone = phone.replace("+", "").replace(" ", "")
        return os.path.join(self.sessions_dir, f"telethon_{safe_phone}")
    
    async def send_code(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"[Telethon] Sending code to {phone}")
        
        session_path = self._get_session_path(phone)
        client = TelegramClient(session_path, self.api_id, self.api_hash)
        
        try:
            await client.connect()
            
            if await client.is_user_authorized():
                duration = time.time() - start_time
                me = await client.get_me()
                logger.info(f"[Telethon] Already logged in: {phone} (ID: {me.id}, duration: {duration:.2f}s)")
                self.active_clients[phone] = client
                return {
                    "status": "already_logged_in",
                    "phone": phone,
                    "user_id": me.id,
                    "duration": duration
                }
            
            sent_code = await client.send_code_request(phone)
            self.phone_code_hashes[phone] = sent_code.phone_code_hash
            self.active_clients[phone] = client
            
            duration = time.time() - start_time
            logger.info(f"[Telethon] Code sent successfully to {phone} (duration: {duration:.2f}s)")
            return {
                "status": "code_sent",
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash,
                "duration": duration
            }
            
        except FloodWaitError as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] FloodWait: {e.seconds} seconds (duration: {duration:.2f}s)")
            try:
                await client.disconnect()
            except:
                pass
            return {"status": "error", "error": f"Please wait {e.seconds} seconds", "duration": duration}
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error sending code: {e} (duration: {duration:.2f}s)")
            try:
                await client.disconnect()
            except:
                pass
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def verify_code(self, phone: str, code: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"[Telethon] Verifying code for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        phone_code_hash = self.phone_code_hashes.get(phone)
        
        try:
            user = await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            duration = time.time() - start_time
            logger.info(f"[Telethon] Successfully signed in: {phone} (User ID: {user.id}, duration: {duration:.2f}s)")
            return {
                "status": "logged_in",
                "user_id": user.id,
                "first_name": user.first_name,
                "phone": phone,
                "duration": duration
            }
            
        except SessionPasswordNeededError:
            duration = time.time() - start_time
            logger.info(f"[Telethon] 2FA required for {phone} (duration: {duration:.2f}s)")
            return {"status": "2fa_required", "phone": phone, "duration": duration}
            
        except PhoneCodeInvalidError:
            duration = time.time() - start_time
            logger.warning(f"[Telethon] Invalid code for {phone} (duration: {duration:.2f}s)")
            return {"status": "error", "error": "Invalid verification code", "duration": duration}
            
        except PhoneCodeExpiredError:
            duration = time.time() - start_time
            logger.warning(f"[Telethon] Code expired for {phone} (duration: {duration:.2f}s)")
            return {"status": "error", "error": "Verification code expired", "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error verifying code: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def verify_2fa(self, phone: str, password: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"[Telethon] Verifying 2FA for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            user = await client.sign_in(password=password)
            duration = time.time() - start_time
            logger.info(f"[Telethon] 2FA verified for {phone} (User ID: {user.id}, duration: {duration:.2f}s)")
            return {
                "status": "logged_in",
                "user_id": user.id,
                "first_name": user.first_name,
                "phone": phone,
                "duration": duration
            }
            
        except PasswordHashInvalidError:
            duration = time.time() - start_time
            logger.warning(f"[Telethon] Invalid 2FA password for {phone} (duration: {duration:.2f}s)")
            return {"status": "error", "error": "Invalid 2FA password", "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error verifying 2FA: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def export_session_string(self, phone: str) -> Optional[str]:
        """Export the current session as a StringSession string for DB storage"""
        start_time = time.time()
        client = self.active_clients.get(phone)
        if not client:
            logger.warning(f"[Telethon] No active client to export session for {phone}")
            return None
        
        try:
            # Save the session to get the auth_key
            client.session.save()
            
            # Create a StringSession from the existing session's auth_key
            string_session = StringSession.save(client.session)
            
            duration = time.time() - start_time
            logger.info(f"[Telethon] Session string exported for {phone} (length: {len(string_session)}, duration: {duration:.2f}s)")
            return string_session
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error exporting session string: {e} (duration: {duration:.2f}s)")
            return None
    
    async def connect_from_string(self, phone: str, session_string: str) -> bool:
        """Connect using a stored session string"""
        start_time = time.time()
        logger.info(f"[Telethon] Connecting from session string for {phone}")
        
        try:
            client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
            await client.connect()
            
            if await client.is_user_authorized():
                self.active_clients[phone] = client
                duration = time.time() - start_time
                logger.info(f"[Telethon] Connected from session string for {phone} (duration: {duration:.2f}s)")
                return True
            else:
                await client.disconnect()
                duration = time.time() - start_time
                logger.warning(f"[Telethon] Session string not authorized for {phone} (duration: {duration:.2f}s)")
                return False
                
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error connecting from session string: {e} (duration: {duration:.2f}s)")
            return False
    
    async def connect_from_file(self, phone: str) -> bool:
        """Connect using a session file"""
        start_time = time.time()
        session_path = self._get_session_path(phone)
        logger.info(f"[Telethon] Connecting from session file for {phone}: {session_path}")
        
        if not os.path.exists(session_path + ".session"):
            logger.warning(f"[Telethon] Session file not found: {session_path}.session")
            return False
        
        try:
            client = TelegramClient(session_path, self.api_id, self.api_hash)
            await client.connect()
            
            if await client.is_user_authorized():
                self.active_clients[phone] = client
                duration = time.time() - start_time
                logger.info(f"[Telethon] Connected from file for {phone} (duration: {duration:.2f}s)")
                return True
            else:
                await client.disconnect()
                return False
                
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error connecting from file: {e} (duration: {duration:.2f}s)")
            return False
    
    async def get_me_info(self, phone: str) -> Dict[str, Any]:
        """Get current user info"""
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            if not client.is_connected():
                await client.connect()
            me = await client.get_me()
            if me:
                return {
                    "status": "success",
                    "id": me.id,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "username": me.username,
                    "phone": me.phone
                }
            return {"status": "error", "error": "Not authorized"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    async def get_authorizations(self, phone: str) -> Dict[str, Any]:
        """Get all active sessions with device info"""
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            result = await client(GetAuthorizationsRequest())
            sessions = []
            current = None
            for auth in result.authorizations:
                info = {
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
                    "is_official_app": auth.official_app if hasattr(auth, 'official_app') else False,
                }
                if auth.current:
                    current = info
                else:
                    sessions.append(info)
            return {
                "status": "success",
                "current_session": current,
                "other_sessions": sessions,
                "total_count": len(sessions) + (1 if current else 0)
            }
        except Exception as e:
            logger.error(f"[Telethon] Error getting authorizations: {e}")
            return {"status": "error", "error": str(e)}
    
    async def disconnect(self, phone: str):
        client = self.active_clients.pop(phone, None)
        if client:
            try:
                await client.disconnect()
                logger.info(f"[Telethon] Disconnected: {phone}")
            except Exception as e:
                logger.error(f"[Telethon] Error disconnecting: {e}")
    
    async def disconnect_all(self):
        for phone in list(self.active_clients.keys()):
            await self.disconnect(phone)
    
    async def log_out(self, phone: str) -> bool:
        """Log out and destroy the session"""
        client = self.active_clients.get(phone)
        if not client:
            return False
        try:
            await client.log_out()
            self.active_clients.pop(phone, None)
            logger.info(f"[Telethon] Logged out: {phone}")
            return True
        except Exception as e:
            logger.error(f"[Telethon] Error logging out: {e}")
            return False


_telethon_manager: Optional[TelethonSessionManager] = None


def get_telethon_manager(api_id: int = None, api_hash: str = None) -> TelethonSessionManager:
    global _telethon_manager
    if _telethon_manager is None:
        if api_id is None or api_hash is None:
            raise ValueError("api_id and api_hash required for first initialization")
        _telethon_manager = TelethonSessionManager(api_id, api_hash)
    return _telethon_manager
