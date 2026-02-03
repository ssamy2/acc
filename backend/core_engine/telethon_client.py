import asyncio
import os
import re
import time
from typing import Optional, Dict, Any
from telethon import TelegramClient
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
        self.sessions_dir = sessions_dir
        self.active_clients: Dict[str, TelegramClient] = {}
        self.phone_code_hashes: Dict[str, str] = {}
        
        os.makedirs(sessions_dir, exist_ok=True)
        logger.info(f"TelethonSessionManager initialized. Sessions dir: {sessions_dir}")
    
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
                logger.info(f"[Telethon] Already logged in: {phone} (duration: {duration:.2f}s)")
                self.active_clients[phone] = client
                me = await client.get_me()
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
            return {"status": "error", "error": f"Please wait {e.seconds} seconds", "duration": duration}
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error sending code: {e} (duration: {duration:.2f}s)")
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
    
    async def get_session_string(self, phone: str) -> Optional[str]:
        start_time = time.time()
        client = self.active_clients.get(phone)
        if not client:
            return None
        
        try:
            from telethon.sessions import StringSession
            
            string_session = StringSession()
            temp_client = TelegramClient(string_session, self.api_id, self.api_hash)
            
            session_path = self._get_session_path(phone)
            duration = time.time() - start_time
            logger.info(f"[Telethon] Session saved at: {session_path}.session (duration: {duration:.2f}s)")
            return session_path
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error getting session string: {e} (duration: {duration:.2f}s)")
            return None
    
    async def connect_from_string(self, phone: str, session_path: str) -> bool:
        start_time = time.time()
        logger.info(f"[Telethon] Connecting from session path for {phone}")
        
        try:
            client = TelegramClient(session_path, self.api_id, self.api_hash)
            await client.connect()
            
            if await client.is_user_authorized():
                self.active_clients[phone] = client
                duration = time.time() - start_time
                logger.info(f"[Telethon] Connected from session for {phone} (duration: {duration:.2f}s)")
                return True
            else:
                await client.disconnect()
                duration = time.time() - start_time
                logger.warning(f"[Telethon] Session not authorized for {phone} (duration: {duration:.2f}s)")
                return False
                
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[Telethon] Error connecting from session: {e} (duration: {duration:.2f}s)")
            return False
    
    async def disconnect(self, phone: str):
        client = self.active_clients.get(phone)
        if client:
            try:
                await client.disconnect()
                del self.active_clients[phone]
                logger.info(f"[Telethon] Disconnected: {phone}")
            except Exception as e:
                logger.error(f"[Telethon] Error disconnecting: {e}")
    
    async def disconnect_all(self):
        for phone in list(self.active_clients.keys()):
            await self.disconnect(phone)


_telethon_manager: Optional[TelethonSessionManager] = None


def get_telethon_manager(api_id: int = None, api_hash: str = None) -> TelethonSessionManager:
    global _telethon_manager
    if _telethon_manager is None:
        if api_id is None or api_hash is None:
            raise ValueError("api_id and api_hash required for first initialization")
        _telethon_manager = TelethonSessionManager(api_id, api_hash)
    return _telethon_manager
