import ctypes
import json
import logging
import sys
import platform
import os
from typing import Optional, Dict, Any

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TDLibClient")

class TDLibClient:
    """
    Low-level wrapper for TDLib JSON interface using ctypes.
    Strictly follows the constraints: No high-level wrappers, raw JSON.
    """
    def __init__(self):
        self._tdjson = self._load_library()
        self.client_id = self._tdjson.td_create_client_id()
        
        # Define return types for C functions
        self._tdjson.td_json_client_receive.restype = ctypes.c_char_p
        self._tdjson.td_json_client_execute.restype = ctypes.c_char_p

    def _load_library(self):
        system = platform.system()
        # Custom Path Logic
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../tdlib"))
        
        try:
            if system == "Windows":
                # Check for explicit path first
                lib_path = os.path.join(base_path, "windows", "tdjson.dll")
                if not os.path.exists(lib_path):
                     lib_path = r"C:\Users\Sami\Desktop\account manger\tdlib\windows\tdjson.dll"
                return ctypes.CDLL(lib_path)
            elif system == "Linux":
                lib_path = os.path.join(base_path, "linux", "libtdjson.so")
                return ctypes.CDLL(lib_path)
            elif system == "Darwin":
                return ctypes.CDLL("libtdjson.dylib")
            else:
                raise OSError("Unsupported Operating System")
        except OSError as e:
            logger.critical("Could not load TDLib library (tdjson). Ensure it is in the PATH or current folder.")
            raise e

    def send(self, query: Dict[str, Any]) -> None:
        """Sends an asynchronous request to TDLib."""
        query_str = json.dumps(query).encode('utf-8')
        self._tdjson.td_json_client_send(self.client_id, query_str)

    def execute(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Executes a synchronous request (only for supported sync methods)."""
        query_str = json.dumps(query).encode('utf-8')
        result = self._tdjson.td_json_client_execute(self.client_id, query_str)
        if result:
            return json.loads(result.decode('utf-8'))
        return None

    def receive(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        Receives an update from TDLib.
        Non-blocking if timeout is small.
        """
        result = self._tdjson.td_json_client_receive(self.client_id, timeout)
        if result:
            return json.loads(result.decode('utf-8'))
        return None

    def destroy(self):
        """Cleanly closes the client."""
        # Does not strictly destroy, but serves as cleanup signal
        # Actual destruction usually handled by sending {'@type': 'close'}
        pass
