"""LNbits API client.

Wraps the LNbits REST API for the wallet operations needed by the USSD
gateway: balance inquiry, invoice generation, payment status checking,
paying invoices and listing recent transactions.
"""

from datetime import datetime
from typing import Dict, List, Optional

import requests
from urllib.parse import urlparse

from src.logging_config import get_logger, log_api_error, mask_secret
from src.models import Invoice, PaymentResult, Transaction

# Hosts allowed over plain HTTP (local development / docker network only).
_DEV_HTTP_HOSTS = {"localhost", "127.0.0.1", "lnbits"}

logger = get_logger(__name__)


class LNbitsAPIError(Exception):
    """Base exception for LNbits API errors.
    
    Error codes used:
    - TIMEOUT: Request exceeded timeout threshold
    - CONNECTION_ERROR: Cannot reach LNbits service
    - INSUFFICIENT_BALANCE: Wallet has insufficient funds for payment
    - HTTP_ERROR: HTTP error response from LNbits API
    - CALLBACK_REJECTED: LNbits refused the invoice's webhook URL
    - INVALID_RESPONSE: Response body is not valid JSON
    """

    def __init__(self, message: str, error_code: Optional[str] = None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class LNbitsClient:
    """API wrapper for LNbits wallet operations."""

    def __init__(self, base_url: str, admin_key: str, invoice_key: str):
        """
        Initialize the LNbits client.

        Args:
            base_url: Base URL of the LNbits instance (e.g. https://lnbits.example.com)
            admin_key: Admin API key (full wallet permissions, used for outgoing payments)
            invoice_key: Invoice/read API key (used for balance and invoice creation)
        
        Raises:
            ValueError: If base_url is not HTTPS (Requirement 8.3)
        """
        # Requirement 8.3: Enforce HTTPS for all LNbits API communication
        # Only allow http:// for localhost/127.0.0.1 (development)
        if not base_url.startswith("https://"):
            parsed = urlparse(base_url)
            if parsed.scheme != "http" or parsed.hostname not in _DEV_HTTP_HOSTS:
                raise ValueError(
                    "LNbits base_url must use HTTPS for security (Requirement 8.3). "
                    f"Got: {base_url}"
                )
            logger.warning(
                f"Using non-HTTPS URL for LNbits: {base_url} - "
                "This should only be used for local development",
                extra={'event_type': 'security_warning', 'base_url': base_url}
            )
        
        # Normalize base_url so endpoint joins are predictable.
        self.base_url = base_url.rstrip("/")
        self.admin_key = admin_key
        self.invoice_key = invoice_key
        self.timeout = 20  # seconds
        logger.info(
            f"LNbitsClient initialized for {self.base_url}",
            extra={
                'event_type': 'initialization',
                'base_url': self.base_url,
                'admin_key': mask_secret(admin_key),
                'invoice_key': mask_secret(invoice_key)
            }
        )

    def get_balance(self, wallet_id: str) -> int:
        """
        Get wallet balance in satoshis.

        The LNbits wallet endpoint reports the balance in millisatoshis, so it
        is converted to satoshis here.

        Args:
            wallet_id: Wallet identifier (used for logging/audit context)

        Returns:
            Wallet balance in satoshis.

        Raises:
            LNbitsAPIError: On API failure or timeout.
        """
        url = f"{self.base_url}/api/v1/wallet"
        headers = {"X-Api-Key": self.invoice_key}

        data = self._request("GET", url, headers=headers)

        balance_msat = data.get("balance", 0)
        balance_sat = int(balance_msat) // 1000
        logger.debug(
            f"Balance for wallet {self._mask(wallet_id)}: {balance_sat} sats",
            extra={
                'event_type': 'balance_query',
                'wallet_id': self._mask(wallet_id),
                'balance_sats': balance_sat
            }
        )
        return balance_sat

    @classmethod
    def create_account(cls, base_url: str, name: str) -> Dict[str, str]:
        """
        Create a new LNbits account with one wallet.

        Each phone number gets its own account, so wallets are isolated from
        one another and from the operator's wallet.

        Args:
            base_url: Base URL of the LNbits instance
            name: Name for the new wallet

        Returns:
            Dict with "wallet_id", "admin_key" and "invoice_key".

        Raises:
            LNbitsAPIError: On API failure or an unexpected response.
        """
        client = cls(base_url=base_url, admin_key="", invoice_key="")
        data = client._request(
            "POST", f"{client.base_url}/api/v1/account", json={"name": name}
        )
        try:
            account = {
                "wallet_id": data["id"],
                "admin_key": data["adminkey"],
                "invoice_key": data["inkey"],
            }
        except (KeyError, TypeError):
            raise LNbitsAPIError("Invalid response from LNbits", "INVALID_RESPONSE")
        logger.info(
            f"Created LNbits account with wallet {client._mask(account['wallet_id'])}",
            extra={'event_type': 'account_created'}
        )
        return account

    def create_invoice(
        self,
        wallet_id: str,
        amount: int,
        memo: str = "USSD Payment",
        webhook: Optional[str] = None,
        unhashed_description: Optional[str] = None,
    ) -> Invoice:
        """
        Create a Lightning invoice for receiving a payment.

        Args:
            wallet_id: Wallet identifier (used for logging/audit context)
            amount: Amount in satoshis
            memo: Invoice description
            webhook: URL LNbits POSTs to once the invoice is paid
            unhashed_description: Hex-encoded description whose SHA-256 goes
                into the invoice (LNURL-pay metadata)

        Returns:
            Invoice object with payment_request and payment_hash.

        Raises:
            LNbitsAPIError: On API failure or timeout.
        """
        url = f"{self.base_url}/api/v1/payments"
        headers = {"X-Api-Key": self.invoice_key}
        payload = {"out": False, "amount": amount, "memo": memo}
        if webhook:
            payload["webhook"] = webhook
        if unhashed_description:
            payload["unhashed_description"] = unhashed_description

        try:
            data = self._request("POST", url, headers=headers, json=payload)
        except LNbitsAPIError as e:
            if e.error_code != "CALLBACK_REJECTED":
                raise
            # The invoice matters more than the paid notification: retry
            # without the webhook (allow the gateway in LNbits' callback URL
            # rules to get SMS alerts back).
            logger.warning(
                f"LNbits rejected the webhook URL; invoice created without it: {e.message}",
                extra={'event_type': 'webhook_rejected'}
            )
            payload = {k: v for k, v in payload.items() if k != "webhook"}
            data = self._request("POST", url, headers=headers, json=payload)

        invoice = Invoice(
            payment_hash=data.get("payment_hash", ""),
            payment_request=data.get("payment_request", data.get("bolt11", "")),
            amount=amount,
            memo=memo,
            created_at=datetime.utcnow(),
            paid=False,
        )
        logger.info(
            f"Created invoice for {amount} sats on wallet {self._mask(wallet_id)} "
            f"(hash: {self._mask(invoice.payment_hash)})"
        )
        return invoice

    def check_invoice_status(self, payment_hash: str) -> Dict:
        """
        Check whether an invoice has been paid.

        Args:
            payment_hash: Payment hash of the invoice to check

        Returns:
            Dict containing at least a "paid" boolean flag and, when available,
            the "amount" in satoshis.

        Raises:
            LNbitsAPIError: On API failure or timeout.
        """
        url = f"{self.base_url}/api/v1/payments/{payment_hash}"
        headers = {"X-Api-Key": self.invoice_key}

        data = self._request("GET", url, headers=headers)

        paid = bool(data.get("paid", False))
        # LNbits reports amounts in millisatoshis; expose satoshis to callers.
        amount_msat = data.get("details", {}).get("amount") if isinstance(data.get("details"), dict) else data.get("amount")
        amount_sat = abs(int(amount_msat)) // 1000 if amount_msat is not None else None

        status = {"paid": paid, "amount": amount_sat, "payment_hash": payment_hash}
        logger.debug(f"Invoice {self._mask(payment_hash)} paid={paid}")
        return status

    def decode_invoice(self, payment_request: str) -> Dict:
        """
        Decode a bolt11 Lightning invoice to extract its details.

        Uses the LNbits decode endpoint to parse the invoice server-side so no
        additional bolt11 dependency is required (Requirement 5.2).

        Args:
            payment_request: The bolt11 invoice string to decode

        Returns:
            Dict with at least "amount" (satoshis, or None when the invoice
            carries no amount) and "payment_hash" (when available).

        Raises:
            LNbitsAPIError: On API failure, timeout, or an undecodable invoice.
        """
        url = f"{self.base_url}/api/v1/payments/decode"
        headers = {"X-Api-Key": self.invoice_key}
        payload = {"data": payment_request}

        data = self._request("POST", url, headers=headers, json=payload)

        # LNbits reports invoice amounts in millisatoshis. Accept the common
        # field spellings and expose satoshis to callers.
        amount_msat = (
            data.get("amount_msat")
            if data.get("amount_msat") is not None
            else data.get("amount")
        )
        amount_sat = abs(int(amount_msat)) // 1000 if amount_msat is not None else None

        decoded = {
            "amount": amount_sat,
            "payment_hash": data.get("payment_hash"),
            "description": data.get("description", "") or "",
        }
        logger.debug(
            f"Decoded invoice (hash: {self._mask(decoded['payment_hash'] or '')}) "
            f"amount={amount_sat}"
        )
        return decoded

    def pay_invoice(self, wallet_id: str, payment_request: str) -> PaymentResult:
        """
        Pay a Lightning invoice.

        Args:
            wallet_id: Wallet identifier (used for logging/audit context)
            payment_request: The bolt11 invoice string to pay

        Returns:
            PaymentResult with a success flag and, on success, payment details.
            On failure, success is False and error_message explains the reason.

        Raises:
            LNbitsAPIError: On timeout or unexpected API failure.
        """
        url = f"{self.base_url}/api/v1/payments"
        headers = {"X-Api-Key": self.admin_key}
        payload = {"out": True, "bolt11": payment_request}

        try:
            data = self._request("POST", url, headers=headers, json=payload)
        except LNbitsAPIError as e:
            # Permanent payment failures (e.g. insufficient balance) are surfaced
            # as an unsuccessful PaymentResult rather than an exception so the
            # menu handler can present a user-facing message.
            if e.error_code in ("INSUFFICIENT_BALANCE", "HTTP_ERROR"):
                logger.warning(
                    f"Payment failed on wallet {self._mask(wallet_id)}: {e.message}"
                )
                return PaymentResult(
                    success=False,
                    payment_hash=None,
                    amount=None,
                    fee=None,
                    error_message=e.message,
                )
            raise

        fee_msat = data.get("fee")
        amount_msat = data.get("amount")
        result = PaymentResult(
            success=True,
            payment_hash=data.get("payment_hash"),
            amount=abs(int(amount_msat)) // 1000 if amount_msat is not None else None,
            fee=abs(int(fee_msat)) // 1000 if fee_msat is not None else None,
            error_message=None,
        )
        logger.info(
            f"Payment sent from wallet {self._mask(wallet_id)} "
            f"(hash: {self._mask(result.payment_hash or '')})"
        )
        return result

    def get_transactions(self, wallet_id: str, limit: int = 5) -> List[Transaction]:
        """
        Get recent transactions for the wallet.

        Args:
            wallet_id: Wallet identifier (used for logging/audit context)
            limit: Maximum number of transactions to return (default: 5)

        Returns:
            List of Transaction objects, most recent first, capped at `limit`.

        Raises:
            LNbitsAPIError: On API failure or timeout.
        """
        url = f"{self.base_url}/api/v1/payments"
        headers = {"X-Api-Key": self.invoice_key}
        # Unpaid invoices and failed payments come back too; fetch extra so
        # `limit` settled ones remain after filtering.
        params = {"limit": limit * 4}

        data = self._request("GET", url, headers=headers, params=params)

        # LNbits returns a list of payment records.
        records = data if isinstance(data, list) else data.get("data", [])
        records = [r for r in records if self._is_settled(r)]

        transactions: List[Transaction] = []
        for record in records[:limit]:
            amount_msat = int(record.get("amount", 0))
            amount_sat = amount_msat // 1000
            # Negative amount indicates an outgoing (sent) payment in LNbits.
            transaction_type = "sent" if amount_msat < 0 else "received"

            transactions.append(
                Transaction(
                    payment_hash=record.get("payment_hash", record.get("checking_id", "")),
                    amount=amount_sat,
                    timestamp=self._parse_timestamp(record.get("time")),
                    memo=record.get("memo", "") or "",
                    transaction_type=transaction_type,
                )
            )

        logger.debug(
            f"Retrieved {len(transactions)} transactions for wallet {self._mask(wallet_id)}"
        )
        return transactions

    def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ):
        """
        Execute an HTTP request against the LNbits API with shared error handling.

        Args:
            method: HTTP method ("GET" or "POST")
            url: Fully-qualified request URL
            headers: Request headers (API key)
            json: JSON body for POST requests
            params: Query string parameters

        Returns:
            Parsed JSON response (dict or list).

        Raises:
            LNbitsAPIError: On timeout, connection error, HTTP error, or
                invalid response payload.
        """
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.Timeout:
            logger.error(f"LNbits request timed out: {method} {url}")
            raise LNbitsAPIError("LNbits request timed out", "TIMEOUT")
        except requests.ConnectionError as e:
            logger.error(f"Cannot reach LNbits: {e}")
            raise LNbitsAPIError("LNbits service unavailable", "CONNECTION_ERROR")
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            detail = self._error_detail(e.response)
            lowered = detail.lower()
            if status_code == 403 or "insufficient balance" in lowered:
                raise LNbitsAPIError("Insufficient balance", "INSUFFICIENT_BALANCE")
            if "callback url" in lowered:
                raise LNbitsAPIError(detail, "CALLBACK_REJECTED")
            logger.error(
                f"LNbits returned HTTP {status_code}: {detail}",
                extra={'event_type': 'api_error', 'service': 'lnbits', 'status_code': status_code}
            )
            # The message reaches the phone screen: LNbits' own explanation,
            # never the request URL.
            raise LNbitsAPIError(detail.rstrip(".") or "Service unavailable", "HTTP_ERROR")
        except ValueError as e:
            # Raised by response.json() on a non-JSON body.
            logger.error(f"Invalid response from LNbits: {e}")
            raise LNbitsAPIError("Invalid response from LNbits", "INVALID_RESPONSE")

    @staticmethod
    def _is_settled(record: Dict) -> bool:
        """True for completed payments (LNbits 1.x "status", older "pending")."""
        status = record.get("status")
        if status is not None:
            return status == "success"
        return record.get("pending") is not True

    @staticmethod
    def _error_detail(response) -> str:
        """Human-readable reason from an LNbits error body ("" if none)."""
        if response is None:
            return ""
        try:
            detail = response.json().get("detail", "")
        except (ValueError, AttributeError):
            return ""
        return detail if isinstance(detail, str) else ""

    @staticmethod
    def _parse_timestamp(value) -> datetime:
        """
        Parse an LNbits timestamp into a datetime.

        LNbits typically reports `time` as a Unix epoch (seconds). Falls back to
        the current time if the value is missing or unparseable.
        """
        if value is None:
            return datetime.utcnow()
        try:
            return datetime.utcfromtimestamp(int(value))
        except (ValueError, TypeError, OSError):
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return datetime.utcnow()

    @staticmethod
    def _mask(value: str) -> str:
        """Mask a sensitive/identifying value for logging (first 8 chars)."""
        if not value:
            return ""
        if len(value) <= 8:
            return "***"
        return f"{value[:8]}..."
