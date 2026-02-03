import multiprocessing
import queue
import logging
import json
import time
import ctypes
import platform
import os
from typing import Dict, Any, Optional

# Re-define raw TDLib wrapper inside the worker to ensure it loads in the new process space
class _TDLibRaw:
    def __init__(self):
        self._lib = self._load_lib()
        
        # Explicitly define signatures for POINTER-BASED interface
        try:
            self._lib.td_json_client_create.restype = ctypes.c_void_p
            self._lib.td_json_client_create.argtypes = []
            
            self._lib.td_json_client_send.restype = None
            self._lib.td_json_client_send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            
            self._lib.td_json_client_receive.restype = ctypes.c_char_p
            self._lib.td_json_client_receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
            
            self._lib.td_json_client_execute.restype = ctypes.c_char_p
            self._lib.td_json_client_execute.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        except AttributeError:
             pass

        # Use Pointer creation
        self.client = self._lib.td_json_client_create()

    def _load_lib(self):
        sys_name = platform.system()
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../tdlib"))
        
        try:
            if sys_name == "Windows":
                # Check for explicit path first
                lib_dir = os.path.join(base_path, "windows")
                lib_path = os.path.join(lib_dir, "tdjson.dll")
                
                if not os.path.exists(lib_path):
                     # Fallback
                     lib_dir = r"C:\Users\Sami\Desktop\account manger\tdlib\windows"
                     lib_path = os.path.join(lib_dir, "tdjson.dll")

                # CRITICAL FIX: Add DLL directory to search path for dependencies (libssl, zlib, etc.)
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(lib_dir)
                    except Exception as e:
                        logging.warning(f"Failed to add_dll_directory: {e}")
                
                # Also change CWD just in case legacy load needs it
                prev_cwd = os.getcwd()
                try:
                    os.chdir(lib_dir)
                    return ctypes.CDLL(lib_path)
                finally:
                    os.chdir(prev_cwd)

            elif sys_name == "Linux":
                lib_path = os.path.join(base_path, "linux", "libtdjson.so")
                return ctypes.CDLL(lib_path)
            elif sys_name == "Darwin":
                lib_path = os.path.join(base_path, "darwin", "libtdjson.dylib")
                return ctypes.CDLL(lib_path)
        except Exception as e:
            raise RuntimeError(f"Could not load tdjson from {base_path}: {e}")

    def send(self, query: Dict[str, Any]):
        data = json.dumps(query).encode('utf-8')
        c_data = ctypes.c_char_p(data)
        # Use simple c_void_p for the client pointer
        self._lib.td_json_client_send(ctypes.c_void_p(self.client), c_data)

    def receive(self, timeout: float = 1.0) -> Optional[Dict]:
        res = self._lib.td_json_client_receive(ctypes.c_void_p(self.client), ctypes.c_double(timeout))
        if res:
            return json.loads(res.decode('utf-8'))
        return None

class TDLibWorker(multiprocessing.Process):
    """
    Dedicated Process for a single TDLib Session.
    Ensures complete isolation of memory and C-state.
    """
    def __init__(self, command_queue: multiprocessing.Queue, result_queue: multiprocessing.Queue, api_id: int, api_hash: str, phone: str):
        super().__init__()
        self.cmd_q = command_queue
        self.res_q = result_queue
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.running = True

    def run(self):
        # This runs in the NEW Process
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(f"TDLibWorker-{self.phone}")
        logger.info("Worker Process Started. Initializing TDLib...")
        
        try:
            # Create necessary directories for TDLib
            import os
            # Sanitize phone for directory name (remove +)
            safe_phone = self.phone.replace("+", "")
            db_dir = f"tdlib_db/{safe_phone}"
            files_dir = f"tdlib_files/{safe_phone}"
            
            os.makedirs(db_dir, exist_ok=True)
            os.makedirs(files_dir, exist_ok=True)
            
            client = _TDLibRaw()
            
            # Basic Config Flow immediately on start
            # 1. Option: Verbosity
            client.send({"@type": "setLogVerbosityLevel", "new_verbosity_level": 1})
            
            # Event Loop
            while self.running:
                # 1. Check for incoming commands (Non-blocking)
                try:
                    cmd_pkg = self.cmd_q.get_nowait()
                    cmd_type = cmd_pkg.get("type")
                    
                    if cmd_type == "STOP":
                        self.running = False
                        break
                    elif cmd_type == "SEND":
                        query = cmd_pkg.get("payload")
                        client.send(query)
                    
                except queue.Empty:
                    pass
                
                # 2. Poll TDLib
                event = client.receive(timeout=0.2)
                if event:
                    self._handle_event(client, event, logger, safe_phone)

        except Exception as e:
            logger.critical(f"CRITICAL WORKER FAILURE: {e}")
            self.res_q.put({"type": "ERROR", "error": str(e)})

    def _handle_event(self, client, event, logger, safe_phone):
        event_type = event.get("@type")
        
        # Verbose Logging for Debugging
        if event_type:
            # Filter out noisy events if needed, but for now log everything relevant
            if event_type not in ["updateOption", "updateSelectedBackground", "updateConnectionState", "updateDiceEmojis"]:
                 logger.info(f"TDLib Event: {json.dumps(event)}")

        # Automatic Auth Flow Handling (Official Method)
        if event_type == "updateAuthorizationState":
            state = event.get("authorization_state", {})
            st_type = state.get("@type")
            
            if st_type == "authorizationStateWaitTdlibParameters":
                logger.info("Applying TDLib Parameters...")
                client.send({
                    "@type": "setTdlibParameters",
                    "use_test_dc": False,
                    "database_directory": f"tdlib_db/{safe_phone}",
                    "files_directory": f"tdlib_files/{safe_phone}",
                    "use_message_database": False, # RAM Optimization
                    "use_chat_info_database": False,
                    "use_secret_chats": False,
                    "api_id": self.api_id,
                    "api_hash": self.api_hash,
                    "system_language_code": "en",
                    "device_model": "EscrowAuditor",
                    "application_version": "1.0",
                    "enable_storage_optimizer": True
                })
            
            elif st_type == "authorizationStateWaitEncryptionKey":
                # Some TDLib versions require this step even for new DBs
                logger.info("Setting Database Encryption Key (Empty)...")
                client.send({
                    "@type": "checkDatabaseEncryptionKey",
                    "encryption_key": "" # Empty key for no encryption
                })

            elif st_type == "authorizationStateWaitPhoneNumber":
                logger.info("Sending Phone Number...")
                client.send({
                     "@type": "setAuthenticationPhoneNumber",
                     "phone_number": self.phone
                })
            
            elif st_type == "authorizationStateWaitCode":
                 # Notify Main Process that we are ready for code
                 logger.info("Waiting for Authentication Code...")
                 self.res_q.put({"type": "STATUS", "status": "WAITING_CODE"})

            elif st_type == "authorizationStateWaitPassword":
                 # Notify Main Process that we are ready for 2FA Password
                 logger.info("Waiting for 2FA Password...")
                 self.res_q.put({"type": "STATUS", "status": "WAITING_PASSWORD"})

            elif st_type == "authorizationStateWaitRegistration":
                 # Notify Main Process that we are ready for Registration (First/Last Name)
                 logger.info("Waiting for User Registration...")
                 self.res_q.put({"type": "STATUS", "status": "WAITING_REGISTRATION"})

            elif st_type == "authorizationStateReady":
                 logger.info("Authorization Successful!")
                 self.res_q.put({"type": "STATUS", "status": "LOGGED_IN"})

        # Pass OTP Codes from Service Messages (777000) to Main Process
        # This is the "Interceptor" Logic running inside the isolated process
        if event_type == "updateNewMessage":
             msg = event.get("message", {})
             sender = msg.get("sender_id", {}).get("user_id")
             if sender == 777000:
                 content = msg.get("content", {}).get("text", {}).get("text", "")
                 self.res_q.put({"type": "INTERCEPTED_OTP", "text": content})

        # Forward specific responses if needed
        if event.get("@extra"):
             self.res_q.put({"type": "RESPONSE", "extra": event.get("@extra"), "data": event})
