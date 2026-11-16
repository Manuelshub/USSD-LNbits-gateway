"""SMS notifications: invoices and incoming-payment alerts.

Messages go to Africa's Talking when an API key is configured. In demo mode
they are also kept in a short per-phone Redis list so the web simulator can
show an "SMS inbox" without a real phone.
"""

import json
import threading
import time
from typing import List, Optional

import redis
import requests

from src.logging_config import get_logger

logger = get_logger(__name__)

_AT_SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
_AT_LIVE_URL = "https://api.africastalking.com/version1/messaging"
_DEMO_INBOX_SIZE = 20
_DEMO_INBOX_TTL = 24 * 3600


class SMSNotifier:
    """Sends SMS to phone numbers; never raises into the USSD flow."""

    def __init__(
        self,
        at_username: str = "",
        at_api_key: str = "",
        sender_id: str = "",
        redis_client: Optional[redis.Redis] = None,
        demo_mode: bool = False,
    ):
        self.at_username = at_username
        self.at_api_key = at_api_key
        self.sender_id = sender_id
        self.redis = redis_client
        self.demo_mode = demo_mode

    @property
    def at_enabled(self) -> bool:
        key = self.at_api_key or ""
        return bool(self.at_username and key and not key.startswith("your_"))

    @property
    def enabled(self) -> bool:
        return self.at_enabled or (self.demo_mode and self.redis is not None)

    def send(self, phone_number: str, message: str) -> None:
        """Queue ``message`` for ``phone_number``.

        The Africa's Talking call runs on a background thread so the USSD
        response is not delayed by the SMS API.
        """
        if self.demo_mode and self.redis is not None:
            self._store_demo(phone_number, message)
        if self.at_enabled:
            threading.Thread(
                target=self._send_at, args=(phone_number, message), daemon=True
            ).start()
        elif not self.demo_mode:
            logger.info(
                "SMS not sent: Africa's Talking not configured",
                extra={'event_type': 'sms_skipped', 'phone_number': phone_number}
            )

    def demo_inbox(self, phone_number: str) -> List[dict]:
        """Most recent demo SMS for ``phone_number`` (newest first)."""
        if self.redis is None:
            return []
        raw = self.redis.lrange(self._demo_key(phone_number), 0, _DEMO_INBOX_SIZE - 1)
        return [json.loads(item) for item in raw]

    def _store_demo(self, phone_number: str, message: str) -> None:
        key = self._demo_key(phone_number)
        try:
            self.redis.lpush(key, json.dumps({"message": message, "time": int(time.time())}))
            self.redis.ltrim(key, 0, _DEMO_INBOX_SIZE - 1)
            self.redis.expire(key, _DEMO_INBOX_TTL)
        except redis.RedisError as e:
            logger.warning(f"Could not store demo SMS: {e}", extra={'event_type': 'sms_error'})

    def _send_at(self, phone_number: str, message: str) -> None:
        url = _AT_SANDBOX_URL if self.at_username == "sandbox" else _AT_LIVE_URL
        data = {"username": self.at_username, "to": phone_number, "message": message}
        if self.sender_id:
            data["from"] = self.sender_id
        try:
            response = requests.post(
                url,
                headers={"apiKey": self.at_api_key, "Accept": "application/json"},
                data=data,
                timeout=15,
            )
            response.raise_for_status()
            logger.info(
                "SMS sent",
                extra={'event_type': 'sms_sent', 'phone_number': phone_number}
            )
        except requests.RequestException as e:
            logger.error(
                f"SMS failed: {e.__class__.__name__}",
                extra={'event_type': 'sms_error', 'phone_number': phone_number}
            )

    @staticmethod
    def _demo_key(phone_number: str) -> str:
        return f"demo_sms:{phone_number}"
