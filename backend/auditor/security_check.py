import logging
from typing import Dict

logger = logging.getLogger("Auditor")

class SecurityAuditor:
    """
    Performs the Deep Security Audit using TDLib events.
    """
    
    @staticmethod
    def audit_passkey_recovery(password_state: Dict) -> bool:
        """
        Returns True if the account is 'Clean' (No 2FA, No Recovery Email).
        Checks fields:
        - has_password
        - has_recovery_email_address
        """
        has_password = password_state.get("has_password", False)
        has_recovery = password_state.get("has_recovery_email_address", False)
        
        if has_password:
            logger.warning("Audit Failed: 2FA (Cloud Password) is ENABLED.")
            return False
        
        if has_recovery:
            logger.warning("Audit Failed: Recovery Email is SET. Account is not safe.")
            return False
            
        logger.info("Audit Passed: No 2FA or Recovery Email found.")
        return True

    @staticmethod
    def audit_sessions(authorizations: Dict) -> bool:
        """
        Returns True if only 1 session exists (the current one).
        """
        sessions = authorizations.get("authorizations", [])
        active_count = len(sessions)
        
        # We expect exactly 1 session (This current bot)
        if active_count == 1:
            logger.info("Audit Passed: Only 1 Active Session.")
            return True
        
        logger.warning(f"Audit Failed: Found {active_count} active sessions. Expected 1.")
        # Optional: Print/Log session details for review
        for session in sessions:
            logger.info(f"Session: {session.get('device_model')} ({session.get('platform')}) - Current: {session.get('is_current')}")
            
        return False
