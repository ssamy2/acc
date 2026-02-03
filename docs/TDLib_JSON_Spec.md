# Raw TDLib JSON Interface Specification & Security Audit

## 1. Core Configuration: `setTdlibParameters`

This is the very first request sent to initialize the TDLib instance.

```json
{
  "@type": "setTdlibParameters",
  "use_test_dc": false,
  "database_directory": "tdlib_db/{session_name}",
  "files_directory": "tdlib_files/{session_name}",
  "use_file_database": true,
  "use_chat_info_database": true,
  "use_message_database": true,
  "use_secret_chats": false,
  "api_id": {API_ID},
  "api_hash": "{API_HASH}",
  "system_language_code": "en",
  "device_model": "EscrowServer",
  "system_version": "1.0",
  "application_version": "1.0",
  "enable_storage_optimizer": true,
  "ignore_file_names": true
}
```

**Memory Optimization Note**: Setting `use_message_database` and `use_chat_info_database` to `false` significantly reduces disk Usage and RAM overhead if you only need Auth and Security checks, but `true` is required if you need to read history for specific audit trails. For the *Interceptor* (OTP extraction), `use_message_database: false` is preferred alongside valid file handlers to keep it lightweight.

## 2. Session Management: `getAuthorizations`

Used to audit active sessions on the account.

**Request:**
```json
{
  "@type": "getAuthorizations"
}
```

**Response Handling:**
The response contains an array of `authorization` objects.
- **Audit Logic:**
    - Iterate through `authorizations`.
    - **Current Session**: Identified by `current: true`.
    - **Zombie Sessions**: Any session where `current: false`.
    - **Security Risk**: If `count(authorizations) > 1`, the account is dirty.

## 3. Deep Security Audit Methods

### A. Passkey & 2FA Check: `getPasswordState`
Determines if a Cloud Password (2FA) or Passkeys are active, and if a recovery email is set.

**Request:**
```json
{
  "@type": "getPasswordState"
}
```

**Key Response Fields (Audit Check):**
- `has_password` (Bool): If `true`, 2FA is enabled.
- `has_recovery_email_address` (Bool): If `true`, the account can be reclaimed by the seller. **Critical Risk**.
- `has_passport_data` (Bool): Indicates if identity documents are uploaded.

### B. Account Cleanup Verification
To verify an account is "Clean":
1. Call `getAuthorizations`. Ensure only 1 active session exists (the bot's session).
2. Call `getPasswordState`. Ensure `has_recovery_email_address` is `false`.
3. Check for specific "official" spam using `searchChatMessages` (optional, for finding past warnings).

## 4. OTP Interception: `updateNewMessage`

The core of the Chained Auth strategy. We listen for `updateNewMessage` on the internal event stream.

**Filter Logic:**
1. Event Type: `updateNewMessage`
2. Sender: `message.sender_id.user_id == 777000` (Telegram Service Notification)
3. Content: `message.content.@type == "messageText"`

**Regex Extraction:**
Pattern to extract the 5-digit login code:
```regex
\b(\d{5})\b
```
*Note: Telegram sometimes sends "Login code: 12345". Be robust against variations.*
