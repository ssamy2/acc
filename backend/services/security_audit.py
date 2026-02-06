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
from config import EMAIL_DOMAIN

logger = get_logger("SecurityAudit")


class TransferMode(Enum):
    """Account transfer modes"""
    MODE_USER_KEEPS_SESSION = "user_keeps_session"  # User keeps 1 session, we change email + password
    MODE_BOT_ONLY = "bot_only"  # User exits, only bot sessions remain


# Our domain for email redirection
OUR_EMAIL_DOMAIN = EMAIL_DOMAIN


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
        
        # ========== Recovery Email Check (2FA) ==========
        # Official Telegram API:
        # - has_recovery_email = True → recovery email is SET and CONFIRMED (pattern hidden!)
        # - email_unconfirmed_pattern → recovery email set but NOT YET confirmed (pattern visible)
        # - recovery_email_full → full email from account.getPasswordSettings (if password known)
        # - login_email_pattern → LOGIN email (completely separate from recovery email!)
        
        has_recovery_email = security_info.get("has_recovery_email", False)
        email_unconfirmed_pattern = security_info.get("email_unconfirmed_pattern")
        recovery_email_full = security_info.get("recovery_email_full")  # Full email if password was provided
        login_email_pattern = security_info.get("login_email_pattern")
        
        if recovery_email_full:
            # We have the full recovery email - check if it's ours
            if OUR_EMAIL_DOMAIN in recovery_email_full.lower():
                log_audit(logger, phone, "Recovery Email (2FA)", True, f"Our email confirmed: {recovery_email_full}")
            else:
                issue = {
                    "type": "RECOVERY_EMAIL_NOT_OURS",
                    "severity": "blocker",
                    "title": "إيميل استرداد 2FA ليس إيميلنا",
                    "description": f"الإيميل الحالي: {recovery_email_full} - يجب تغييره لإيميلنا",
                    "action": f"يجب تغيير إيميل استرداد 2FA إلى: {actions_needed.get('our_email', 'N/A')}",
                    "current_email": recovery_email_full,
                    "target_email": actions_needed.get("our_email"),
                    "auto_fixable": True
                }
                issues.append(issue)
                actions_needed["change_email"] = True
                log_audit(logger, phone, "Recovery Email (2FA)", False, f"NOT our email: {recovery_email_full}")
        
        elif email_unconfirmed_pattern:
            # Recovery email is pending confirmation - check the pattern
            if OUR_EMAIL_DOMAIN in str(email_unconfirmed_pattern).lower():
                log_audit(logger, phone, "Recovery Email (2FA)", True, f"Our email pending confirmation: {email_unconfirmed_pattern}")
                # Email is ours but needs confirmation - auto-fixable
                issue = {
                    "type": "RECOVERY_EMAIL_PENDING_CONFIRMATION",
                    "severity": "action_required",
                    "title": "إيميل الاسترداد بانتظار التأكيد",
                    "description": f"الإيميل {email_unconfirmed_pattern} ينتظر كود التأكيد",
                    "action": "سيتم تأكيد الإيميل تلقائياً عند استلام الكود",
                    "auto_fixable": True
                }
                issues.append(issue)
            else:
                issue = {
                    "type": "RECOVERY_EMAIL_WRONG_PENDING",
                    "severity": "blocker",
                    "title": "إيميل استرداد خاطئ بانتظار التأكيد",
                    "description": f"الإيميل المعلق: {email_unconfirmed_pattern} - ليس إيميلنا",
                    "action": f"يجب إلغاء الإيميل المعلق وتغييره إلى: {actions_needed.get('our_email', 'N/A')}",
                    "current_email": email_unconfirmed_pattern,
                    "target_email": actions_needed.get("our_email"),
                    "auto_fixable": True
                }
                issues.append(issue)
                actions_needed["change_email"] = True
                log_audit(logger, phone, "Recovery Email (2FA)", False, f"Wrong pending email: {email_unconfirmed_pattern}")
        
        elif has_recovery_email:
            # Recovery email is confirmed but we DON'T know what it is
            # (pattern is hidden when confirmed in account.getPassword)
            # If we don't have the password to check, we must flag this as unknown
            issue = {
                "type": "RECOVERY_EMAIL_UNKNOWN",
                "severity": "blocker",
                "title": "إيميل استرداد مؤكد لكن غير معروف",
                "description": "يوجد إيميل استرداد مؤكد لكن لا نستطيع التحقق منه بدون كلمة المرور",
                "action": f"يجب تغيير إيميل الاسترداد إلى: {actions_needed.get('our_email', 'N/A')}",
                "target_email": actions_needed.get("our_email"),
                "auto_fixable": True  # Will be changed during finalize
            }
            issues.append(issue)
            actions_needed["change_email"] = True
            log_audit(logger, phone, "Recovery Email (2FA)", False, "Confirmed but unknown - must change")
        
        else:
            # No recovery email at all - we'll set one during finalize
            log_audit(logger, phone, "Recovery Email (2FA)", True, "No recovery email - will set ours during finalize")
            actions_needed["change_email"] = True
        
        # ========== Login Email Check (separate from recovery!) ==========
        # login_email_pattern is for "Sign in with email" feature
        # This is a completely different email from the 2FA recovery email
        if login_email_pattern:
            issue = {
                "type": "LOGIN_EMAIL_EXISTS",
                "severity": "action_required",
                "title": f"يوجد إيميل تسجيل دخول: {login_email_pattern}",
                "description": "إيميل تسجيل الدخول (ميزة منفصلة عن إيميل استرداد 2FA) - يُفضل إزالته",
                "action": "إزالة إيميل تسجيل الدخول من: الإعدادات > الخصوصية والأمان > تسجيل الدخول بالإيميل",
                "current_email": login_email_pattern,
                "auto_fixable": False
            }
            issues.append(issue)
            log_audit(logger, phone, "Login Email", False, f"Login email exists: {login_email_pattern}")
        else:
            log_audit(logger, phone, "Login Email", True, "No login email set")
        
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
                    "title": f"Must terminate {other_count} session(s) manually",
                    "description": "BOT_ONLY mode - All sessions must be terminated from Telegram app (24h restriction)",
                    "action": "Go to Settings > Devices > Terminate other sessions",
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
