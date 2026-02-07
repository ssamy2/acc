# Authentication & Session Management — Flow Specification

> **Version:** 6.0 — Updated 2026-02-08

---

## Overview

The system authenticates Telegram accounts via **Pyrogram** (primary) and **Telethon** (backup).
All sessions are **in-memory only** (no disk files). Session strings are stored in the database.

### Email Requirement
- **All recovery emails MUST end with `@channelsseller.site`.**
- Emails are auto-generated per account using an HMAC-SHA256 hash of the Telegram ID.
- The system rejects any email that doesn't match this domain.

---

## Login Flow

### Step 1: `POST /api/v1/auth/init`
- User provides phone number + transfer mode.
- Per-phone `asyncio.Lock` acquired (prevents concurrent inits for the same number).
- System checks for existing active sessions → if found, skips to "already authenticated".
- Otherwise, sends verification code via Pyrogram `send_code()`.

### Step 2: `POST /api/v1/auth/verify` (code)
- User provides the SMS/Telegram code.
- Per-phone lock acquired.
- **If login succeeds (no 2FA):** Response includes `has_2fa: false`. Frontend skips the manual email step and proceeds directly to Audit → Finalize. The Finalize step will auto-setup 2FA + recovery email.
- **If 2FA is required:** Response includes `has_2fa: true`. User is prompted for their 2FA password.

### Step 2b: `POST /api/v1/auth/verify` (password)
- User provides 2FA password → authenticated.
- Password is cached in RAM for use during Finalize.
- Response includes `has_2fa: true`. Frontend shows the email step so the user can verify/change recovery email.

### Step 3: Email Step (only if 2FA was already ON)
- `POST /api/v1/email/confirm/{phone}` — checks current recovery email status.
- If 2FA is **not enabled**, returns `status: "2fa_not_enabled"` with a hint to skip.
- If 2FA is enabled, verifies recovery email is `@channelsseller.site`.

### Step 4: `GET /api/v1/account/audit/{phone}`
- Runs security audit: checks 2FA, recovery email, session count, delete requests.

### Step 5: `POST /api/v1/account/finalize/{phone}`
- Per-phone lock acquired.

#### If 2FA was NOT enabled (auto-setup):
1. `enable_2fa(password, email=target_email)` — enables 2FA + sets recovery email in **one API call** (per Telegram official docs: `account.updatePasswordSettings`).
2. Telegram returns `EMAIL_UNCONFIRMED_X` → handled as success.
3. Waits up to 25s for email verification code via webhook.
4. Confirms with `account.confirmPasswordEmail`.
5. Exports Pyrogram session string.
6. Creates Telethon session (send code → intercept from Pyrogram 777000 → sign in → 2FA with new password).
7. Saves both session strings to DB.

#### If 2FA was already enabled:
1. Changes password to a new generated one.
2. Checks current recovery email → if not ours, changes it.
3. Waits for email confirmation code → confirms.
4. Creates Telethon session.
5. Saves both session strings to DB.

---

## Concurrency Model

- Each phone number gets its own `asyncio.Lock` via `_get_auth_lock(phone)`.
- **10+ simultaneous logins** for different numbers work without interference (each has independent state in `active_clients` dict).
- Same-phone concurrent requests are serialized via the lock.
- Finalize also uses a separate per-phone lock from `PyrogramSessionManager._get_lock()`.

---

## Error Recovery Matrix

| Error | Cause | Recovery Action |
| :--- | :--- | :--- |
| `PHONE_CODE_EXPIRED` | Time elapsed | User requests new code via `init_auth`. |
| `FLOOD_WAIT_X` | API rate limit | Parse X seconds, return to user with wait time. |
| `SESSION_PASSWORD_NEEDED` | 2FA Enabled | Return `2fa_required` → user provides password. |
| `EMAIL_UNCONFIRMED_X` | Email set, needs code | Treated as success. Wait for webhook code. |
| `AUTH_KEY_UNREGISTERED` | Session expired | Return session_dead status. Re-auth needed. |
| Session timeout (30 min) | Inactivity | Return 408. User starts over. Session saved as backup. |

---

## Database Schema

### Table: `escrow_accounts`
| Column | Type | Description |
| :--- | :--- | :--- |
| `phone` | TEXT PK | E.164 format |
| `telegram_id` | INTEGER | Telegram user ID |
| `status` | TEXT | Auth status enum |
| `pyrogram_session` | TEXT | Pyrogram StringSession |
| `telethon_session` | TEXT | Telethon StringSession |
| `generated_password` | TEXT | Auto-generated 2FA password |
| `target_email` | TEXT | Must be `@channelsseller.site` |
| `email_hash` | TEXT | HMAC hash for webhook matching |
| `has_2fa` | BOOLEAN | Whether 2FA was enabled at login |
| `transfer_mode` | TEXT | `BOT_ONLY` or `USER_KEEPS_SESSION` |
