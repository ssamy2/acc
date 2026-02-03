# Telegram Escrow Auditor - API V3 Documentation

## Overview

نظام إدارة حسابات تيليجرام للـ Escrow مع دعم:
- تشفير الهاش للإيميلات
- وضعين للتحويل (bot_only / user_keeps_session)
- إدارة الإيميل والتحقق من الكود
- مراقبة صحة الجلسات
- نظام التسليم مع fallback

---

## Base URLs

```
Development: http://localhost:8001/api/v1
Webhook: http://localhost:8001/api3
```

---

## Authentication Flow

### 1. Initialize Auth
```http
POST /api/v1/auth/init
Content-Type: application/json

{
    "phone": "+201234567890",
    "transfer_mode": "bot_only"  // or "user_keeps_session"
}
```

**Response:**
```json
{
    "status": "code_sent",
    "message": "Verification code sent to Telegram",
    "phone_code_hash": "abc123...",
    "transfer_mode": "bot_only"
}
```

### 2. Verify Auth (Code or 2FA)
```http
POST /api/v1/auth/verify
Content-Type: application/json

// For code verification:
{
    "phone": "+201234567890",
    "code": "12345"
}

// For 2FA verification:
{
    "phone": "+201234567890",
    "password": "user_2fa_password"
}
```

**Response:**
```json
{
    "status": "authenticated",
    "telegram_id": 123456789,
    "target_email": "email-for-xY9kL3mN12@channelsseller.site",
    "email_hash": "xY9kL3mN12"
}
```

---

## Email Management

### Get Target Email
```http
GET /api/v1/email/target/{phone}
```

**Response:**
```json
{
    "status": "success",
    "account_id": "+201234567890",
    "target_email": "email-for-xY9kL3mN12@channelsseller.site",
    "email_hash": "xY9kL3mN12",
    "instructions": "User should change their Telegram recovery email to this address"
}
```

### Check Email Code
```http
GET /api/v1/email/code/{phone}?wait_seconds=5
```

**Response (code received):**
```json
{
    "status": "received",
    "code": "12345",
    "message": "Verification code received"
}
```

**Response (waiting):**
```json
{
    "status": "waiting",
    "message": "Code not received yet",
    "email_hash": "xY9kL3mN12"
}
```

### Confirm Email Changed
```http
POST /api/v1/email/confirm/{phone}
```

**Response:**
```json
{
    "status": "success",
    "message": "Email change verified",
    "email_changed": true,
    "current_pattern": "e***l@channelsseller.site"
}
```

---

## Account Audit

### Run Security Audit
```http
GET /api/v1/account/audit/{phone}
```

**Response:**
```json
{
    "passed": true,
    "issues_count": 0,
    "issues": [],
    "actions_needed": {},
    "account_id": "+201234567890",
    "telegram_id": 123456789,
    "target_email": "email-for-xY9kL3mN12@channelsseller.site",
    "email_changed": true,
    "transfer_mode": "bot_only"
}
```

### Finalize Account
```http
POST /api/v1/account/finalize/{phone}
Content-Type: application/json

{
    "confirm_email_changed": true,
    "two_fa_password": "current_password_if_exists"
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Account finalized successfully",
    "password": "NewGeneratedP@ssw0rd!",
    "transfer_mode": "bot_only",
    "terminated_sessions": 3
}
```

---

## Session Health

### Check Session Health
```http
GET /api/v1/sessions/health/{phone}
```

**Response:**
```json
{
    "status": "healthy",
    "checks": {
        "pyrogram_session": {"valid": true},
        "telethon_session": {"valid": true},
        "email_unchanged": true,
        "sessions_count": 2,
        "sessions_ok": true,
        "has_delete_request": false
    },
    "needs_regeneration": false,
    "needs_attention": false
}
```

### Regenerate Sessions
```http
POST /api/v1/sessions/regenerate/{phone}
```

**Response:**
```json
{
    "status": "success",
    "message": "Sessions regenerated",
    "results": {
        "pyrogram_regenerated": true,
        "telethon_regenerated": false
    }
}
```

---

## Delivery

### Request Delivery Code
```http
POST /api/v1/delivery/request-code/{phone}
```

**Response:**
```json
{
    "status": "success",
    "message": "Delivery code sent",
    "two_fa_password": "StoredP@ssw0rd!",
    "fallback_seconds": 20
}
```

### Confirm Delivery
```http
POST /api/v1/delivery/confirm/{phone}
Content-Type: application/json

{
    "received": true
}
```

**Response:**
```json
{
    "status": "success",
    "message": "Delivery #1 confirmed",
    "delivery_number": 1
}
```

---

## Email Webhook (Cloudflare)

### Receive Email
```http
POST /api3/webhook
Content-Type: application/json

{
    "from": "noreply@telegram.org",
    "to": "email-for-xY9kL3mN12@channelsseller.site",
    "hash": "xY9kL3mN12",
    "subject": "Telegram Login Code",
    "body": "Your verification code is 12345..."
}
```

### Get Code by Hash
```http
GET /api3/webhook/code/{hash}?timeout=60
```

### List All Codes (Debug)
```http
GET /api3/webhook/codes
```

### Health Check
```http
GET /api3/webhook/health
```

---

## Transfer Modes

### bot_only
- المستخدم يخرج من جميع جلساته
- تبقى جلسات البوت فقط (Pyrogram + Telethon)
- تحكم كامل في الحساب

### user_keeps_session
- المستخدم يحتفظ بجلسة واحدة
- يتم تغيير الإيميل وكلمة المرور
- مشاركة الوصول مع البوت

---

## Email Hash System

### How it works:
1. يتم تشفير `telegram_id` باستخدام HMAC-SHA256
2. يتم تحويله لـ Base64 URL-safe
3. يتم أخذ أول 12 حرف
4. يتم حفظ الـ mapping في `logs/hash_mappings.json`

### Email Format:
```
email-for-{encrypted_hash}@channelsseller.site
```

### Example:
```
Telegram ID: 123456789
Hash: xY9kL3mN12
Email: email-for-xY9kL3mN12@channelsseller.site
```

---

## Database Schema

### Account Table
| Field | Type | Description |
|-------|------|-------------|
| id | Integer | Primary Key |
| phone | String(20) | Phone number (unique) |
| telegram_id | Integer | Telegram user ID |
| transfer_mode | Enum | bot_only / user_keeps_session |
| email_hash | String(50) | Encrypted hash for email |
| target_email | String(255) | Our email for this account |
| email_changed | Boolean | Whether email changed to ours |
| email_verified | Boolean | Whether we verified the change |
| delivery_count | Integer | How many times delivered |
| pyrogram_healthy | Boolean | Pyrogram session status |
| telethon_healthy | Boolean | Telethon session status |
| has_delete_request | Boolean | Account deletion pending |
| generated_password | String(255) | Our generated 2FA password |

---

## Error Codes

| Status | Description |
|--------|-------------|
| 400 | Bad Request - Invalid input |
| 404 | Not Found - Account not found |
| 500 | Internal Server Error |

---

## Security Notes

1. **Hash Secret Key**: Set `HASH_SECRET_KEY` environment variable in production
2. **Credentials Log**: Stored in `logs/credentials.log` and `logs/credentials.json`
3. **Hash Mappings**: Stored in `logs/hash_mappings.json`
4. **Session Strings**: Stored encrypted in database

---

## Flow Diagram

```
1. User enters phone → /auth/init
2. User enters code → /auth/verify (code)
3. If 2FA required → /auth/verify (password)
4. Get target email → /email/target
5. User changes email in Telegram
6. Webhook receives code → /api3/webhook
7. Frontend checks code → /email/code
8. User enters code in Telegram
9. Confirm email changed → /email/confirm
10. Run security audit → /account/audit
11. Finalize account → /account/finalize
12. Ready for delivery
```
