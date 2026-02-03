import asyncio
import re
import logging
from typing import Optional, Dict

logger = logging.getLogger("Interceptor")

class OTPListener:
    """
    Listens to TDLib events to intercept login codes for Telethon/Pyrogram.
    """
    def __init__(self):
        self._otp_queue = asyncio.Queue()
        self._active = True
        self.telegram_service_id = 777000

    async def feed_event(self, event: Dict):
        """
        Feeds a raw JSON event from TDLib into the listener.
        """
        # Listen for updateNewMessage
        if event.get("@type") == "updateNewMessage":
            message = event.get("message", {})
            sender_id = message.get("sender_id", {})
            
            # Check if Sender is Telegram Service (777000)
            if sender_id.get("user_id") == self.telegram_service_id:
                content = message.get("content", {})
                
                # Check for Text Content
                if content.get("@type") == "messageText":
                    text_obj = content.get("text", {})
                    text_body = text_obj.get("text", "")
                    
                    logger.info("Received message from Telegram Service. Attempting extraction...")
                    code = self._extract_code(text_body)
                    
                    if code:
                        logger.info(f"Interceptor: Captured Code {code}")
                        await self._otp_queue.put(code)

    def _extract_code(self, text: str) -> Optional[str]:
        # Regex for 5-digit code.
        # \b ensures word boundaries so we don't pick up part of a phone number or ID.
        match = re.search(r'\b(\d{5})\b', text)
        if match:
            return match.group(1)
        return None

    async def wait_for_code(self, timeout: int = 60) -> str:
        """
        Blocks until a code is intercepted or timeout occurs.
        """
        try:
            return await asyncio.wait_for(self._otp_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for OTP code.")
            raise ValueError("OTP Timeout")
