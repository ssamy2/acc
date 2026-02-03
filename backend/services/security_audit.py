"""
Security Audit Service
Audits Telegram accounts for security requirements before transfer

Two Modes Available:
1. MODE_BOT_ONLY (Default): 
   - User sells account, bot receives it
   - User must terminate sessions MANUALLY (24h Telegram restriction)
   - Email change is mandatory before transfer
   - Bot gets full control after transfer

2. MODE_USER_KEEPS_SESSION:
   - Bot sells account, user receives it
   - Sessions can be terminated automatically
   - User keeps one session for receiving the account
   - Auto session termination is available
"""

from typing import Dict, List, Tuple, Any
from enum import Enum
from backend.core_engine.logger import get_logger, log_audit
from backend.core_engine.credentials_logger import get_full_email_info

logger = get_logger("SecurityAudit")


class TransferMode(Enum):
    """Account transfer modes"""
    MODE_USER_KEEPS_SESSION = "user_keeps_session"  # User keeps 1 session, we change email + password
    MODE_BOT_ONLY = "bot_only"  # User exits, only bot sessions remain


# Our domain for email redirection
OUR_EMAIL_DOMAIN = "channelsseller.site"


class SecurityAuditService:
    
    @staticmethod
    def run_audit(
        security_info: Dict[str, Any], 
        phone: str,
        mode: TransferMode = TransferMode.MODE_BOT_ONLY,
        telegram_id: int = None
    ) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any]]:
        """
        Run security audit on account
        
        Args:
            security_info: Security information from pyrogram
            phone: Phone number
            mode: Transfer mode (user_keeps_session or bot_only)
            telegram_id: Telegram user ID (used to generate our email)
        
        Returns:
            Tuple of (passed, issues, actions_needed)
        """
        issues = []
        actions_needed = {
            "change_email": False,
            "change_password": False,
            "terminate_sessions": False,
            "our_email": None,
            "mode": mode.value
        }
        
        # Generate our email address for this account using encrypted hash
        if telegram_id:
            email_info = get_full_email_info(telegram_id)
            our_email = email_info["email"]
            our_hash = email_info["hash"]
            actions_needed["our_email"] = our_email
            actions_needed["email_hash"] = our_hash
        
        # ========== 2FA Check ==========
        has_password = security_info.get("has_password", False)
        if has_password:
            log_audit(logger, phone, "2FA Check", True, "2FA is enabled - password will be changed")
            actions_needed["change_password"] = True
        else:
            log_audit(logger, phone, "2FA Check", True, "No 2FA set - will enable with new password")
            actions_needed["change_password"] = True  # We'll set a new password
        
        # ========== Email Check ==========
        has_recovery_email = security_info.get("has_recovery_email", False)
        email_unconfirmed_pattern = security_info.get("email_unconfirmed_pattern")
        login_email_pattern = security_info.get("login_email_pattern")
        
        # Current email pattern (masked)
        current_email = email_unconfirmed_pattern or login_email_pattern or None
        
        if has_recovery_email or email_unconfirmed_pattern:
            # Check if it's already our email
            if current_email and OUR_EMAIL_DOMAIN in str(current_email):
                log_audit(logger, phone, "Recovery Email Check", True, f"Already using our email: {current_email}")
            else:
                issue = {
                    "type": "RECOVERY_EMAIL_CHANGE_REQUIRED",
                    "severity": "action_required",
                    "title": "Recovery email must be changed",
                    "description": f"Current email pattern: {current_email or 'confirmed but hidden'}. Must change to our email.",
                    "action": f"Change recovery email to: {actions_needed.get('our_email', 'email-for-S<ID>@' + OUR_EMAIL_DOMAIN)}",
                    "current_email": current_email,
                    "target_email": actions_needed.get("our_email"),
                    "auto_fixable": True
                }
                issues.append(issue)
                actions_needed["change_email"] = True
                log_audit(logger, phone, "Recovery Email Check", False, f"Email change required: {current_email} -> our email")
        else:
            # No recovery email set - we need to set one
            log_audit(logger, phone, "Recovery Email Check", True, "No recovery email - will set our email")
            actions_needed["change_email"] = True
        
        # ========== Login Email Check ==========
        if login_email_pattern:
            if OUR_EMAIL_DOMAIN in str(login_email_pattern):
                log_audit(logger, phone, "Login Email Check", True, f"Already using our login email: {login_email_pattern}")
            else:
                issue = {
                    "type": "LOGIN_EMAIL_CHANGE_REQUIRED",
                    "severity": "action_required",
                    "title": f"Login email detected ({login_email_pattern})",
                    "description": "Login email should be changed or removed",
                    "action": "Change or remove login email in Settings > Privacy & Security > Email Login",
                    "current_email": login_email_pattern,
                    "auto_fixable": False  # Login email is different from recovery email
                }
                issues.append(issue)
                log_audit(logger, phone, "Login Email Check", False, f"Login email: {login_email_pattern}")
        else:
            log_audit(logger, phone, "Login Email Check", True, "No login email set")
        
        # ========== Sessions Check ==========
        other_sessions = security_info.get("other_sessions", [])
        other_count = len(other_sessions)
        
        if mode == TransferMode.MODE_BOT_ONLY:
            # BOT_ONLY mode: Sessions must be terminated MANUALLY by user
            # Reason: Telegram requires 24h wait before session can be terminated programmatically
            # User must terminate sessions from their Telegram app
            if other_count > 0:
                session_details = []
                for s in other_sessions:
                    detail = f"{s.get('device_model', 'Unknown')} - {s.get('app_name', 'Unknown')} ({s.get('country', 'Unknown')})"
                    session_details.append(detail)
                
                issue = {
                    "type": "TERMINATE_SESSIONS_MANUAL",
                    "severity": "blocker",
                    "title": f"يجب إنهاء {other_count} جلسة يدوياً",
                    "description": "وضع BOT_ONLY - يجب إنهاء جميع الجلسات من تطبيق تيليجرام (قيود 24 ساعة)",
                    "action": "اذهب إلى الإعدادات > الأجهزة > إنهاء الجلسات الأخرى",
                    "sessions": session_details,
                    "auto_fixable": False  # NOT auto-fixable due to 24h restriction
                }
                issues.append(issue)
                actions_needed["terminate_sessions"] = False  # User must do it manually
                log_audit(logger, phone, "Sessions Check (BOT_ONLY)", False, f"{other_count} sessions - user must terminate manually")
            else:
                log_audit(logger, phone, "Sessions Check (BOT_ONLY)", True, "No other sessions")
        
        elif mode == TransferMode.MODE_USER_KEEPS_SESSION:
            # USER_KEEPS_SESSION mode: We can terminate sessions automatically
            # User is receiving the account, so we have permission to terminate
            if other_count > 0:
                session_details = []
                for s in other_sessions:
                    detail = f"{s.get('device_model', 'Unknown')} - {s.get('app_name', 'Unknown')} ({s.get('country', 'Unknown')})"
                    session_details.append(detail)
                
                issue = {
                    "type": "TERMINATE_SESSIONS_AUTO",
                    "severity": "action_required",
                    "title": f"{other_count} session(s) will be terminated",
                    "description": "Mode: USER_KEEPS_SESSION - Sessions can be terminated automatically",
                    "action": "Will terminate other sessions automatically",
                    "sessions": session_details,
                    "auto_fixable": True  # Auto-fixable in this mode
                }
                issues.append(issue)
                actions_needed["terminate_sessions"] = True
                log_audit(logger, phone, "Sessions Check (USER_KEEPS)", False, f"{other_count} sessions to auto-terminate")
            else:
                log_audit(logger, phone, "Sessions Check (USER_KEEPS)", True, "No other sessions")
        
        # ========== Determine Pass/Fail ==========
        # In the new flow, "issues" are actions that need to be taken
        # Most are auto-fixable, so we can proceed
        auto_fixable_issues = [i for i in issues if i.get("auto_fixable", False)]
        manual_issues = [i for i in issues if not i.get("auto_fixable", False)]
        
        # Pass if no manual issues (auto-fixable issues will be handled)
        passed = len(manual_issues) == 0
        
        if passed:
            if auto_fixable_issues:
                logger.info(f"AUDIT PASSED for {phone} ({len(auto_fixable_issues)} auto-fixable actions pending)")
            else:
                logger.info(f"AUDIT PASSED for {phone} (no issues)")
        else:
            logger.warning(f"AUDIT REQUIRES MANUAL ACTION for {phone}: {len(manual_issues)} manual issues")
        
        return passed, issues, actions_needed
    
    @staticmethod
    def format_audit_report(
        passed: bool, 
        issues: List[Dict[str, Any]], 
        actions_needed: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Format audit results for API response"""
        
        auto_fixable = [i for i in issues if i.get("auto_fixable", False)]
        manual = [i for i in issues if not i.get("auto_fixable", False)]
        
        return {
            "passed": passed,
            "issues_count": len(issues),
            "auto_fixable_count": len(auto_fixable),
            "manual_action_count": len(manual),
            "issues": issues,
            "actions_needed": actions_needed or {},
            "can_proceed": passed,
            "message": _get_audit_message(passed, issues, actions_needed)
        }


def _get_audit_message(passed: bool, issues: List, actions_needed: Dict) -> str:
    """Generate human-readable audit message"""
    if not issues:
        return "Account ready for transfer. No changes needed."
    
    auto_fixable = [i for i in issues if i.get("auto_fixable", False)]
    manual = [i for i in issues if not i.get("auto_fixable", False)]
    
    if passed and auto_fixable:
        return f"Account ready. {len(auto_fixable)} automatic action(s) will be performed: " + \
               ", ".join([a.get("type", "action") for a in auto_fixable])
    
    if manual:
        return f"{len(manual)} manual action(s) required: " + \
               ", ".join([m.get("title", "action") for m in manual])
