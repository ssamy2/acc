import multiprocessing
import logging
from typing import Dict, Tuple, Optional
from backend.core_engine.isolated_worker import TDLibWorker

logger = logging.getLogger("SessionManager")

class SessionManager:
    _instance = None

    def __init__(self):
        # Maps phone -> (WorkerProcess, cmd_queue, res_queue)
        self.active_sessions: Dict[str, Tuple[multiprocessing.Process, multiprocessing.Queue, multiprocessing.Queue]] = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_session(self, phone: str, api_id: int, api_hash: str):
        if phone in self.active_sessions:
            logger.warning(f"Session for {phone} already active.")
            return

        cmd_q = multiprocessing.Queue()
        res_q = multiprocessing.Queue()
        
        worker = TDLibWorker(cmd_q, res_q, api_id, api_hash, phone)
        worker.start()
        
        self.active_sessions[phone] = (worker, cmd_q, res_q)
        logger.info(f"Started Worker for {phone}")

    def get_session(self, phone: str):
        return self.active_sessions.get(phone)

    def stop_session(self, phone: str):
        if phone in self.active_sessions:
            worker, cmd_q, res_q = self.active_sessions[phone]
            cmd_q.put({"type": "STOP"})
            worker.join(timeout=2)
            if worker.is_alive():
                worker.terminate()
            del self.active_sessions[phone]

    def send_code(self, phone: str, code: str):
        # This is for manual input if needed, but the worker handles automation.
        pass

# Global Accessor
session_manager = SessionManager.get_instance()
