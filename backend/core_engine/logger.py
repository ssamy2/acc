import logging
import os
from datetime import datetime
from typing import Optional

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOGS_DIR, f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


class ColoredFormatter(logging.Formatter):
    
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging():
    
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    root_logger.handlers = []
    
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = ColoredFormatter(
        '%(asctime)s | %(levelname)-8s | %(name)-15s | %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_request(logger: logging.Logger, method: str, endpoint: str, data: dict = None):
    logger.info(f"REQUEST: {method} {endpoint}")
    if data:
        logger.debug(f"  Data: {data}")


def log_response(logger: logging.Logger, status: int, data: dict = None):
    logger.info(f"RESPONSE: Status {status}")
    if data:
        logger.debug(f"  Data: {data}")


def log_auth_step(logger: logging.Logger, phone: str, step: str, status: str, details: str = None):
    msg = f"AUTH [{phone}] Step: {step} | Status: {status}"
    if details:
        msg += f" | {details}"
    logger.info(msg)


def log_audit(logger: logging.Logger, phone: str, check: str, passed: bool, details: str = None):
    status = "PASSED" if passed else "FAILED"
    msg = f"AUDIT [{phone}] {check}: {status}"
    if details:
        msg += f" | {details}"
    
    if passed:
        logger.info(msg)
    else:
        logger.warning(msg)


_root_logger = setup_logging()
_root_logger.info(f"Logging initialized. Log file: {LOG_FILE}")
