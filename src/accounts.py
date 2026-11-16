"""Phone-number accounts: registration, PINs and lockout.

Each registered phone number owns a separate LNbits account. The wallet keys,
a PBKDF2 hash of the user's PIN and their language live in the
``phone_wallet:{phone}`` record managed by :class:`SessionManager`.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional

import redis

from src.lnbits_client import LNbitsClient
from src.logging_config import get_logger
from src.models import WalletConfig
from src.phone import mask_phone
from src.session_manager import SessionManager, SessionStoreError

logger = get_logger(__name__)

PIN_LENGTH = 4
_PBKDF2_ITERATIONS = 200_000
# PINs rejected at creation because they are the first ones an attacker tries.
_WEAK_PINS = {"1234", "4321", "0123", "3210", "1212", "2580", "0852"}
# Invoices created over USSD/LNURL expire well within a day.
_INVOICE_OWNER_TTL = 24 * 3600


@dataclass
class PinCheck:
    """Outcome of a PIN verification."""
    ok: bool
    locked: bool = False
    attempts_left: int = 0


def validate_new_pin(pin: str) -> Optional[str]:
    """Return an error message for an unacceptable new PIN, else None."""
    if pin is None or len(pin) != PIN_LENGTH or not pin.isdigit():
        return "PIN must be 4 digits."
    if len(set(pin)) == 1 or pin in _WEAK_PINS:
        return "PIN too easy to guess."
    return None


def hash_pin(pin: str, salt_hex: Optional[str] = None) -> tuple:
    """Hash a PIN with PBKDF2-SHA256. Returns ``(salt_hex, hash_hex)``."""
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def pin_matches(pin: str, salt_hex: str, hash_hex: str) -> bool:
    """Constant-time comparison of ``pin`` against a stored hash."""
    if not salt_hex or not hash_hex or not pin:
        return False
    _, candidate = hash_pin(pin, salt_hex)
    return hmac.compare_digest(candidate, hash_hex)


class AccountService:
    """Registration, PIN checks and preferences for phone-number wallets."""

    def __init__(
        self,
        session_manager: SessionManager,
        lnbits_url: str,
        max_pin_attempts: int = 3,
        lockout_seconds: int = 1800,
    ):
        self.session_manager = session_manager
        self.redis = session_manager.redis
        self.lnbits_url = lnbits_url
        self.max_pin_attempts = max_pin_attempts
        self.lockout_seconds = lockout_seconds

    def get(self, phone_number: str) -> Optional[WalletConfig]:
        """Wallet record for a phone number, or None when unregistered."""
        return self.session_manager.get_wallet_for_phone(phone_number)

    def client_for(self, wallet: WalletConfig) -> LNbitsClient:
        """LNbits client scoped to ``wallet``."""
        return LNbitsClient(
            base_url=self.lnbits_url,
            admin_key=wallet.admin_key,
            invoice_key=wallet.invoice_key,
        )

    def register(
        self, phone_number: str, pin_salt: str, pin_hash: str, language: str
    ) -> WalletConfig:
        """Create an LNbits account for ``phone_number`` and store it.

        The PIN arrives already hashed (see :func:`hash_pin`) because the
        registration flow keeps it in the session between two screens.

        Raises:
            LNbitsAPIError: If LNbits cannot create the account.
            SessionStoreError: If the record cannot be stored.
        """
        existing = self.get(phone_number)
        if existing is not None:
            return existing

        account = LNbitsClient.create_account(
            self.lnbits_url, name=f"USSD {mask_phone(phone_number)}"
        )
        wallet = WalletConfig(
            phone_number=phone_number,
            wallet_id=account["wallet_id"],
            admin_key=account["admin_key"],
            invoice_key=account["invoice_key"],
            pin_hash=pin_hash,
            pin_salt=pin_salt,
            language=language,
        )
        self.session_manager.set_wallet_for_phone(wallet)
        logger.info(
            "Registered new phone wallet",
            extra={'event_type': 'registration', 'phone_number': phone_number}
        )
        return wallet

    def verify_pin(self, wallet: WalletConfig, pin: str) -> PinCheck:
        """Check ``pin`` for ``wallet``, counting failures towards a lockout.

        After ``max_pin_attempts`` consecutive failures the phone is locked
        for ``lockout_seconds``; a correct PIN resets the counter.
        """
        key = self._attempts_key(wallet.phone_number)
        try:
            failures = int(self.redis.get(key) or 0)
            if failures >= self.max_pin_attempts:
                return PinCheck(ok=False, locked=True)

            if pin_matches(pin, wallet.pin_salt, wallet.pin_hash):
                self.redis.delete(key)
                return PinCheck(ok=True, attempts_left=self.max_pin_attempts)

            failures = self.redis.incr(key)
            self.redis.expire(key, self.lockout_seconds)
        except redis.RedisError as e:
            raise SessionStoreError(f"Session store unavailable: {e}")

        logger.warning(
            "Wrong PIN entered",
            extra={
                'event_type': 'pin_failure',
                'phone_number': wallet.phone_number,
                'failures': failures,
            }
        )
        left = max(self.max_pin_attempts - failures, 0)
        return PinCheck(ok=False, locked=left == 0, attempts_left=left)

    def set_pin(self, wallet: WalletConfig, pin_salt: str, pin_hash: str) -> WalletConfig:
        """Replace the stored PIN hash for ``wallet``."""
        wallet.pin_salt, wallet.pin_hash = pin_salt, pin_hash
        self.session_manager.set_wallet_for_phone(wallet)
        logger.info(
            "PIN changed",
            extra={'event_type': 'pin_changed', 'phone_number': wallet.phone_number}
        )
        return wallet

    def set_language(self, wallet: WalletConfig, language: str) -> WalletConfig:
        wallet.language = language
        self.session_manager.set_wallet_for_phone(wallet)
        return wallet

    def remember_invoice(self, payment_hash: str, phone_number: str) -> None:
        """Record who an invoice belongs to, for the paid-invoice webhook."""
        try:
            self.redis.setex(self._invoice_key(payment_hash), _INVOICE_OWNER_TTL, phone_number)
        except redis.RedisError as e:
            logger.warning(f"Could not record invoice owner: {e}", extra={'event_type': 'invoice_owner_error'})

    def invoice_owner(self, payment_hash: str) -> Optional[WalletConfig]:
        """Wallet that created the invoice with ``payment_hash``, if known."""
        try:
            phone_number = self.redis.get(self._invoice_key(payment_hash))
        except redis.RedisError as e:
            raise SessionStoreError(f"Session store unavailable: {e}")
        return self.get(phone_number) if phone_number else None

    def forget_invoice(self, payment_hash: str) -> None:
        try:
            self.redis.delete(self._invoice_key(payment_hash))
        except redis.RedisError:
            pass

    @property
    def lockout_minutes(self) -> int:
        return max(self.lockout_seconds // 60, 1)

    @staticmethod
    def _attempts_key(phone_number: str) -> str:
        return f"pin_failures:{phone_number}"

    @staticmethod
    def _invoice_key(payment_hash: str) -> str:
        return f"invoice_owner:{payment_hash}"
