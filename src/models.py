"""Data models for USSD-LNbits Gateway."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List


class MenuState(Enum):
    """Menu navigation states."""
    MAIN_MENU = "main_menu"
    CHECK_BALANCE = "check_balance"
    BALANCE_DISPLAY = "balance_display"
    RECEIVE_PAYMENT = "receive_payment"
    AMOUNT_INPUT = "amount_input"
    INVOICE_DISPLAY = "invoice_display"
    CHECK_PAYMENT_STATUS = "check_payment_status"
    SEND_PAYMENT = "send_payment"
    INVOICE_INPUT = "invoice_input"
    CONFIRM_PAYMENT = "confirm_payment"
    PAYMENT_RESULT = "payment_result"
    TRANSACTION_HISTORY = "transaction_history"
    HISTORY_DISPLAY = "history_display"
    EXIT = "exit"
    # Registration of a new phone number
    REGISTER_LANGUAGE = "register_language"
    REGISTER_PIN = "register_pin"
    REGISTER_PIN_CONFIRM = "register_pin_confirm"
    REGISTERED = "registered"
    # Receive / send submenus
    RECEIVE_MENU = "receive_menu"
    SEND_MENU = "send_menu"
    RECIPIENT_INPUT = "recipient_input"
    SEND_AMOUNT_INPUT = "send_amount_input"
    PIN_ENTRY = "pin_entry"
    # Settings
    SETTINGS_MENU = "settings_menu"
    LANGUAGE_SELECT = "language_select"
    CHANGE_PIN_OLD = "change_pin_old"
    CHANGE_PIN_NEW = "change_pin_new"
    CHANGE_PIN_CONFIRM = "change_pin_confirm"


@dataclass
class Session:
    """USSD session state."""
    session_id: str
    phone_number: str
    wallet_id: str
    current_state: MenuState
    state_data: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    language: str = "en"
    
    def to_json(self) -> dict:
        """Serialize to JSON for Redis storage."""
        return {
            "session_id": self.session_id,
            "language": self.language,
            "phone_number": self.phone_number,
            "wallet_id": self.wallet_id,
            "current_state": self.current_state.value,
            "state_data": _encode_state(self.state_data),
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat()
        }
    
    @classmethod
    def from_json(cls, data: dict) -> "Session":
        """Deserialize from JSON."""
        return cls(
            session_id=data["session_id"],
            phone_number=data["phone_number"],
            wallet_id=data["wallet_id"],
            current_state=MenuState(data["current_state"]),
            state_data=_decode_state(data.get("state_data", {})),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_activity=datetime.fromisoformat(data["last_activity"]),
            language=data.get("language", "en"),
        )


@dataclass
class WalletConfig:
    """Wallet credentials for a phone number.

    ``pin_hash``/``pin_salt`` hold a PBKDF2 hash of the user's PIN (hex); both
    are empty for mappings created before PINs existed or by an operator.
    """
    phone_number: str
    wallet_id: str
    admin_key: str
    invoice_key: str
    pin_hash: str = ""
    pin_salt: str = ""
    language: str = "en"
    
    def to_json(self) -> dict:
        """Serialize to JSON for Redis storage."""
        return {
            "phone_number": self.phone_number,
            "wallet_id": self.wallet_id,
            "admin_key": self.admin_key,
            "invoice_key": self.invoice_key,
            "pin_hash": self.pin_hash,
            "pin_salt": self.pin_salt,
            "language": self.language,
        }
    
    @classmethod
    def from_json(cls, data: dict) -> "WalletConfig":
        """Deserialize from JSON."""
        return cls(
            phone_number=data["phone_number"],
            wallet_id=data["wallet_id"],
            admin_key=data["admin_key"],
            invoice_key=data["invoice_key"],
            pin_hash=data.get("pin_hash", ""),
            pin_salt=data.get("pin_salt", ""),
            language=data.get("language", "en"),
        )


@dataclass
class Invoice:
    """Lightning invoice details."""
    payment_hash: str
    payment_request: str
    amount: int  # satoshis
    memo: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    paid: bool = False
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "payment_hash": self.payment_hash,
            "payment_request": self.payment_request,
            "amount": self.amount,
            "memo": self.memo,
            "created_at": self.created_at.isoformat(),
            "paid": self.paid
        }


@dataclass
class PaymentResult:
    """Result of a payment attempt."""
    success: bool
    payment_hash: Optional[str] = None
    amount: Optional[int] = None
    fee: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class Transaction:
    """Transaction history entry."""
    payment_hash: str
    amount: int  # satoshis (positive for receive, negative for send)
    timestamp: datetime
    memo: str
    transaction_type: str  # "received" or "sent"
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "payment_hash": self.payment_hash,
            "amount": self.amount,
            "timestamp": self.timestamp.isoformat(),
            "memo": self.memo,
            "transaction_type": self.transaction_type
        }


@dataclass
class MenuResponse:
    """Response from menu handler."""
    display_text: str
    continue_session: bool  # True for CON, False for END
    updated_session: Session


class InputType(Enum):
    """Expected input types for validation."""
    MENU_CHOICE = "menu_choice"
    NUMERIC_AMOUNT = "numeric_amount"
    INVOICE_STRING = "invoice_string"


@dataclass
class ValidationResult:
    """Input validation result."""
    valid: bool
    parsed_value: Optional[Any] = None
    error_message: Optional[str] = None


# --- Session state_data codec -------------------------------------------------
# MenuHandler keeps Invoice / Transaction / PaymentResult objects in
# Session.state_data. Redis stores JSON, so these are tagged on the way out and
# rebuilt on the way in. Other values (str, int, bool, None) pass through.

_TYPE_TAG = "__type__"


def _encode_state(value: Any) -> Any:
    """Convert state_data values into JSON-serializable form."""
    if isinstance(value, Invoice):
        return {_TYPE_TAG: "Invoice", **value.to_dict()}
    if isinstance(value, Transaction):
        return {_TYPE_TAG: "Transaction", **value.to_dict()}
    if isinstance(value, PaymentResult):
        return {
            _TYPE_TAG: "PaymentResult",
            "success": value.success,
            "payment_hash": value.payment_hash,
            "amount": value.amount,
            "fee": value.fee,
            "error_message": value.error_message,
        }
    if isinstance(value, datetime):
        return {_TYPE_TAG: "datetime", "value": value.isoformat()}
    if isinstance(value, dict):
        return {k: _encode_state(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_state(v) for v in value]
    return value


def _decode_state(value: Any) -> Any:
    """Rebuild objects encoded by _encode_state."""
    if isinstance(value, dict):
        tag = value.get(_TYPE_TAG)
        if tag == "Invoice":
            return Invoice(
                payment_hash=value["payment_hash"],
                payment_request=value["payment_request"],
                amount=value["amount"],
                memo=value["memo"],
                created_at=datetime.fromisoformat(value["created_at"]),
                paid=value.get("paid", False),
            )
        if tag == "Transaction":
            return Transaction(
                payment_hash=value["payment_hash"],
                amount=value["amount"],
                timestamp=datetime.fromisoformat(value["timestamp"]),
                memo=value["memo"],
                transaction_type=value["transaction_type"],
            )
        if tag == "PaymentResult":
            return PaymentResult(
                success=value["success"],
                payment_hash=value.get("payment_hash"),
                amount=value.get("amount"),
                fee=value.get("fee"),
                error_message=value.get("error_message"),
            )
        if tag == "datetime":
            return datetime.fromisoformat(value["value"])
        return {k: _decode_state(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_state(v) for v in value]
    return value
