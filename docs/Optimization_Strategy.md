# Optimization Strategy: Concurrency & Resource Management

## Architecture The Constraints
The user requires running TDLib, Telethon, and Pyrogram potentially in parallel or sequentially without system interference (deadlocks, race conditions) and high RAM usage.

### 1. Process Isolation vs. Single Process (Asyncio)

**Recommendation: Single Process with Asyncio (Event Loop Integration)**

Why?
- **RAM**: Running 3 separate Python interpreters (Processes) triples the base memory footprint (approx 30-50MB per empty process + library overhead).
- **Complexity**: IPC (Inter-Process Communication) is complex and prone to latency.
- **TDLib Nature**: TDLib takes care of its own thread pool internally. The Python wrapper just needs to poll it.
- **Asyncio**: Telethon and Pyrogram are native asyncio libraries. The raw TDLib wrapper can be made "async-compatible" by polling in a non-blocking way.

**If strict isolation is required (e.g. for massive scaling):**
Use `multiprocessing.Process`.
- Each Account Handler runs in its own Process.
- **RAM Saving**: Use specific TDLib parameters:
    - `use_message_database: false` (Disables caching chats to disk/RAM)
    - `use_chat_info_database: false`
    - `enable_storage_optimizer: true`

### 2. Threading Strategy for TDLib (The Blocking C-Interface)

TDLib's `client_receive` is blocking or uses a timeout.
**Correct Pattern**:
Run the TDLib `receive` loop in a **separate thread** (`threading.Thread`) inside the main process.
- This thread pushes events into a thread-safe `asyncio.Queue`.
- The Main Async Loop consumes this Queue.
- This prevents the blocking C-call from freezing the Telethon/Pyrogram asyncio loops.

### 3. RAM Optimization Configuration
To minimize footprint:
1.  **Garbage Collection**: Explicitly `del` heavy objects and call `gc.collect()` after the Audit phase is done.
2.  **TDLib Config**:
    ```json
    "use_message_database": false,
    "use_secret_chats": false,
    "files_directory": "/tmp/ramdisk/..." 
    ```
    (Using a temporary directory or RAM disk for cache prevents disk I/O lag and clears up automatically).

### 4. Avoiding Interference
- **Session Locking**: Ensure the Database (SQLite) is in WAL mode (`PRAGMA journal_mode=WAL;`) to allow concurrent reads/writes without locking errors.
- **Rate Limiting**: Do not fire requests from all 3 clients instantly. Add a random jitter (0.5s - 2s) delay between actions.
