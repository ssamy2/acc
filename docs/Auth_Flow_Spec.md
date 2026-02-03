# Chained Authentication & Interception Architecture

## The Three-Login Strategy

To secure the account completely, we authenticate three independent clients. This redundancy ensures we have fallback access and different protocol footprints.

### Step 1: TDLib (The Master Session)
- **Role**: Primary controller, Event Listener.
- **Method**: Standard `setAuthenticationPhoneNumber` -> `checkAuthenticationCode` flow via `td_json_client`.
- **Status**: Once logged in, it enters a "Listening" state.

### Step 2: Telethon (The Secondary Session)
- **Action**: Trigger `TelethonClient.send_code_request(phone)`.
- **Interception**:
    - The code is **not** sent via SMS (usually) if an active session exists. It goes to the TDLib session (Telegram Chat 777000).
    - **TDLib Event Listener**: Detects `updateNewMessage` from `777000`.
    - **Extraction**: Parses "12345" from the message body.
    - **Forwarding**: The Python script passes this code to the waiting Telethon `sign_in` method.

### Step 3: Pyrogram (The Backup Session)
- **Action**: Trigger `PyrogramClient.connect()` and send code request.
- **Interception**: Repeat the interception process. Note that requesting a new code might invalidate the previous one, so this must be done sequentially.

---

## Interception Logic (The "Engine")

The Engine uses an `asyncio` event loop to handle blocking operations without freezing.

**Workflow:**
1. **State**: `WAITING_FOR_CODE` (Global/Context Variable).
2. **Listener**: A dedicated coroutine reading `td_json_client_receive`.
    - It uses a short timeout (e.g., 1.0s) to yield control back to the event loop.
3. **Trigger**: When Telethon requests a code, we set a flag `expecting_otp = True`.
4. **Match**: The Listener sees a message from `777000`. If `expecting_otp` is True, it extracts the regex match.
5. **Callback**: The code is pushed to an `asyncio.Queue` which the Telethon login flow is `await`ing.

## Database Schema (Normalized)

### Table: `accounts`
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER PK | Local ID |
| `phone` | TEXT | E.164 format |
| `is_clean` | BOOLEAN | Result of Deep Audit |
| `created_at` | DATETIME | |

### Table: `sessions`
| Column | Type | Description |
| :--- | :--- | :--- |
| `account_id` | FK | Link to accounts |
| `library` | TEXT | 'tdlib', 'telethon', 'pyrogram' |
| `session_data` | BLOB | Encrypted Session String / Path |
| `api_id` | INT | App ID used |
| `device_hash` | TEXT | For fingerprinting |

## Error Recovery Matrix

| Error | Cause | Recovery Action |
| :--- | :--- | :--- |
| `PHONE_CODE_EXPIRED` | Time elapsed | Trigger `resend_code` immediately. |
| `FLOOD_WAIT_X` | API Spam | Parse `X` seconds. Sleep strict `X + 5`. Switch IP if possible. |
| `SESSION_PASSWORD_NEEDED` | 2FA Enabled | Prompt User (if interactive) or Fail Audit (if strict auto-escrow). |
| `Code sent via SMS` | No active session | Detect via `type` in authState. Fallback to manual input/API input. |
