# Changelog V5 - Email Verification Logic Fix + Security Review

## Date: 2026-02-06

---

## 🔴 Critical Bug Fix: Email Verification Logic

### Root Cause
The code was incorrectly treating `login_email_pattern` (Telegram's "Sign in with email" feature) as the 2FA recovery email. These are **completely separate** features in Telegram's API.

### Official Telegram API (`account.password`):
| Field | Meaning |
|-------|---------|
| `has_recovery` | Recovery email is SET and CONFIRMED (pattern hidden!) |
| `email_unconfirmed_pattern` | Recovery email set but NOT YET confirmed (pattern visible) |
| `login_email_pattern` | LOGIN email - completely separate from recovery! |

### Solution
Added `get_recovery_email_full()` method that uses `account.getPasswordSettings(password)` with SRP to retrieve the **full recovery email address** (not just a pattern).

---

## Files Modified

### Backend

#### `backend/core_engine/pyrogram_client.py`
- **NEW**: `_ensure_connected()` - Helper to ensure client is connected
- **NEW**: `get_recovery_email_full()` - Gets full recovery email via `account.getPasswordSettings` with SRP
- **FIXED**: `get_security_info()` - Now accepts `known_password` param, correctly separates recovery vs login email, returns `recovery_email_full`
- **FIXED**: `get_last_telegram_code()` - Now supports 5 AND 6 digit codes, filters by `max_age_seconds` to avoid stale codes
- **ADDED**: `FloodWait` handling in `verify_code()` and `verify_2fa()`

#### `backend/services/security_audit.py`
- **FIXED**: Complete rewrite of email checks - properly separates recovery email (2FA) from login email
- **FIXED**: Uses `EMAIL_DOMAIN` from config instead of hardcoded string
- **NEW**: 4 recovery email states: `full_known`, `pending`, `confirmed_unknown`, `none`
- **NEW**: `LOGIN_EMAIL_EXISTS` issue type for login email (separate from recovery)

#### `backend/api/routes.py`
- **FIXED**: `audit_account` - passes `known_password` to `get_security_info`, correct email status determination
- **FIXED**: `finalize_account` - uses `recovery_email_full` instead of `login_email_pattern`
- **FIXED**: `confirm_email_changed` - uses correct fields from `get_security_info`
- **FIXED**: `sessions/health` - uses `recovery_email_full` instead of non-existent `recovery_email_pattern`
- **FIXED**: `admin/account` - shows recovery email and login email separately
- **NEW**: `email/code-fallback` - Reads code from Telegram messages (777000) as fallback
- **NEW**: `email/confirm-code` - Confirms pending recovery email with verification code
- **SECURITY**: API credentials now imported from `config.py` (not hardcoded)
- **SECURITY**: `EMAIL_DOMAIN` used from config everywhere (not hardcoded)
- **SECURITY**: `delivery/request-code` no longer exposes `generated_password` in response
- **FIXED**: `delivery/confirm` - Now properly logs out Pyrogram AND Telethon sessions BEFORE clearing from DB

#### `backend/api/webhook_routes.py`
- **SECURITY**: Added `CODE_EXPIRY_SECONDS = 600` - codes expire after 10 minutes
- **FIXED**: `get_code_by_hash()` now checks code expiry

### Frontend

#### `frontend/index_main.html`
- **NEW**: Email status panel showing live recovery email + login email status
- **NEW**: Refresh email status button
- **NEW**: Manual code entry section (for entering codes from logs)
- **IMPROVED**: Better button layout and flow

#### `frontend/app_main.js`
- **NEW**: `refreshEmailStatus()` - Fetches and displays live email status
- **NEW**: `submitManualEmailCode()` - Allows manual code entry
- **FIXED**: `displayEmailInstructions()` - Now fetches live status on load
- **FIXED**: `confirmEmailChanged()` - Handles new response format
- **FIXED**: `checkEmailCode()` - Fallback to Telegram messages if webhook fails

#### `frontend/receive.html`
- **FIXED**: Shows recovery email (2FA) and login email separately
- **FIXED**: Correct status indicators (confirmed/pending/unknown/none)

#### `frontend/style_main.css`
- **NEW**: `.email-status-panel` styles
- **NEW**: `.severity-blocker` and `.severity-action_required` styles

---

## Security Improvements (Phase 1)
1. API credentials from `config.py` only (not hardcoded in routes)
2. `EMAIL_DOMAIN` centralized in config
3. No password exposure in delivery endpoint
4. Code expiry (10 min) prevents stale code reuse
5. FloodWait handling prevents API abuse
6. Proper session logout before deletion (prevents orphaned Telegram sessions)
7. Recovery email vs login email correctly separated (prevents false "email changed" status)

---

## Phase 2: Delivery Security + Deep Security Check (2026-02-06)

### `backend/api/delivery.py`
- **SECURITY**: Removed `two_fa_password` from `request-code` response (replaced with `has_2fa_password: bool`)
- **FIXED**: `confirm` now properly loads and logs out BOTH Pyrogram + Telethon sessions before clearing from DB
- **ADDED**: `get_telethon()` helper for Telethon session management

### `backend/services/delivery_service.py`
- **SECURITY**: Removed `password` from `get_received_code` response (replaced with `has_password: bool`)

### `backend/api/routes.py`
- **NEW**: `GET /security/check/{account_id}` - Deep security check with:
  - Device info analysis (API ID, device model, platform, app name)
  - Red flag detection (suspicious sessions, email changes, 2FA disabled, pending resets)
  - Auto-freeze on critical threats (changes 2FA password, terminates suspicious sessions)
  - Threat levels: `safe`, `low`, `warning`, `critical`
- **NEW**: `GET /admin/connections` - Monitor active Pyrogram/Telethon connections in memory
- **NEW**: `POST /admin/connections/cleanup` - Clean up dead connections to free RAM

### `backend/api/sessions.py`
- **FIXED**: `get_account_emails_live()` - accepts `known_password`, returns correct field names
- **FIXED**: `sessions/emails` endpoint - uses new field names (`recovery_email_full`, `is_our_recovery_email`)
- **FIXED**: `sessions/info` endpoint - same field name fixes

### `backend/api/audit.py`
- **FIXED**: `get_account_emails_live()` - same fixes as sessions.py

### `backend/api/admin.py`
- **FIXED**: Account details use new field names (`recovery_email`, `is_our_recovery_email`, `login_email_pattern`)

### API Credentials Cleanup (8 files)
Removed hardcoded `API_ID`/`API_HASH` from:
- `admin.py`, `auth.py`, `delivery.py`, `delivery_service.py`
- (Previously fixed: `routes.py`, `audit.py`, `sessions.py`, `credentials_logger.py`, `transfer_service.py`)

---

## Phase 3: Connection Management + RAM Protection (2026-02-06)

### `backend/core_engine/pyrogram_client.py`
- **NEW**: `cleanup_inactive_clients()` - Disconnects dead/unresponsive clients to free RAM
- **NEW**: `get_active_count()` - Returns number of active clients in memory

### `backend/main_v2.py`
- **NEW**: Periodic cleanup task runs every 5 minutes (configurable via `CLEANUP_INTERVAL_SECONDS`)
- Cleans both Pyrogram and Telethon dead connections automatically
- Properly cancels cleanup task on shutdown

### Frontend Fixes

#### `frontend/app_main.js`
- **FIXED**: `refreshEmailStatus()` - No longer uses hardcoded domain, uses `email_matches` from API response
- **FIXED**: `sendCode()` - Handles `already_authenticated`, `already_logged_in` statuses (skips to correct step)

#### `frontend/receive.html`
- **FIXED**: Uses `is_our_recovery_email` instead of old `is_our_email` field
- **FIXED**: Delivery API paths corrected (removed double `/api/v2` prefix)
- **SECURITY**: Removed password display from delivery code response

#### `frontend/dashboard.html`
- **FIXED**: All API paths updated to use correct `/api/v1/` base
- **FIXED**: Delivery flow uses correct endpoints (`delivery/request-code`, `delivery/confirm`)
- **SECURITY**: No longer displays raw 2FA password in step 4
- **FIXED**: `forceSecure` now uses `/security/check` endpoint
- **FIXED**: Account loading uses `/admin/accounts/all`
