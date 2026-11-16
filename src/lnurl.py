"""Lightning addresses (LNURL-pay, LUD-06/LUD-16) and the paid-invoice webhook.

Every registered phone number gets the address ``<digits>@<PUBLIC_URL host>``,
so anyone with a Lightning wallet can pay a feature-phone user. LNbits calls
the webhook when an invoice created here or over USSD is paid, and the owner
gets an SMS.
"""

import hmac
import json
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.i18n import tr
from src.lnbits_client import LNbitsAPIError
from src.logging_config import get_logger, log_audit_payment
from src.phone import mask_phone, username_to_phone
from src.session_manager import SessionStoreError

logger = get_logger(__name__)

router = APIRouter()

MIN_SENDABLE_MSAT = 1_000
MAX_SENDABLE_MSAT = 10_000_000 * 1_000  # 10M sats per payment


def _app_state():
    # Imported lazily: src.main includes this router, so a top-level import
    # would be circular.
    from src import main

    return main


def lightning_domain(public_url: str) -> str:
    """Host (and port, if any) of ``public_url``; "" when unset."""
    return urlparse(public_url).netloc if public_url else ""


def _metadata(username: str, domain: str) -> str:
    address = f"{username}@{domain}"
    return json.dumps([
        ["text/plain", f"Payment to {mask_phone('+' + username)} via USSD wallet"],
        ["text/identifier", address],
    ])


def _lnurl_error(reason: str, status_code: int = 200) -> JSONResponse:
    # LUD-06 errors are 200 responses with status=ERROR; wallets show `reason`.
    return JSONResponse({"status": "ERROR", "reason": reason}, status_code=status_code)


def _wallet_for_username(username: str):
    state = _app_state()
    if state.config is None or not state.config.public_url or state.accounts is None:
        return None, None
    phone_number = username_to_phone(username)
    if phone_number is None:
        return state, None
    return state, state.accounts.get(phone_number)


@router.get("/.well-known/lnurlp/{username}")
def lnurlp(username: str):
    """LUD-16: resolve ``username@domain`` to a payRequest."""
    try:
        state, wallet = _wallet_for_username(username)
    except SessionStoreError:
        return _lnurl_error("Service temporarily unavailable", 503)
    if state is None:
        raise HTTPException(status_code=404, detail="Lightning addresses are disabled")
    if wallet is None:
        return _lnurl_error("Unknown Lightning address", 404)

    domain = lightning_domain(state.config.public_url)
    return {
        "tag": "payRequest",
        "callback": f"{state.config.public_url}/lnurlp/{username}/callback",
        "minSendable": MIN_SENDABLE_MSAT,
        "maxSendable": MAX_SENDABLE_MSAT,
        "metadata": _metadata(username, domain),
        "commentAllowed": 0,
    }


@router.get("/lnurlp/{username}/callback")
def lnurlp_callback(username: str, amount: int = Query(...)):
    """LUD-06 callback: create an invoice on the phone owner's wallet."""
    if amount < MIN_SENDABLE_MSAT or amount > MAX_SENDABLE_MSAT:
        return _lnurl_error("Amount out of range")
    if amount % 1000:
        return _lnurl_error("Amount must be a whole number of sats")

    try:
        state, wallet = _wallet_for_username(username)
    except SessionStoreError:
        return _lnurl_error("Service temporarily unavailable", 503)
    if state is None:
        raise HTTPException(status_code=404, detail="Lightning addresses are disabled")
    if wallet is None:
        return _lnurl_error("Unknown Lightning address", 404)

    sats = amount // 1000
    metadata = _metadata(username, lightning_domain(state.config.public_url))
    try:
        invoice = state.accounts.client_for(wallet).create_invoice(
            wallet.wallet_id,
            sats,
            memo="Lightning address payment",
            webhook=webhook_url(state.config),
            unhashed_description=metadata.encode().hex(),
        )
    except LNbitsAPIError as e:
        logger.error(
            f"LNURL invoice failed: {e.message}",
            extra={'event_type': 'lnurl_error', 'phone_number': wallet.phone_number}
        )
        return _lnurl_error("Could not create invoice")

    state.accounts.remember_invoice(invoice.payment_hash, wallet.phone_number)
    log_audit_payment(
        logger,
        operation="lnurl_invoice_created",
        phone_number=wallet.phone_number,
        amount=sats,
        payment_hash=invoice.payment_hash,
        success=True,
    )
    return {"pr": invoice.payment_request, "routes": []}


def webhook_url(config) -> Optional[str]:
    """URL LNbits calls when an invoice is paid (None without a secret)."""
    if not config.webhook_secret:
        return None
    return f"{config.webhook_base_url}/webhooks/lnbits/{config.webhook_secret}"


@router.post("/webhooks/lnbits/{secret}")
async def lnbits_paid_webhook(secret: str, request: Request):
    """Paid-invoice callback from LNbits: SMS the invoice owner.

    The secret in the path keeps outsiders from calling it, and the payment is
    re-checked against LNbits before anyone is notified.
    """
    state = _app_state()
    cfg = state.config
    if cfg is None or not cfg.webhook_secret or not hmac.compare_digest(secret, cfg.webhook_secret):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        body = await request.json()
        # LNbits 1.6 posts json=payment.json(), i.e. a JSON string that
        # itself holds the payment object.
        if isinstance(body, str):
            body = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    payment_hash = body.get("payment_hash") if isinstance(body, dict) else None
    if not payment_hash or not isinstance(payment_hash, str):
        raise HTTPException(status_code=400, detail="Missing payment_hash")

    try:
        owner = state.accounts.invoice_owner(payment_hash)
    except SessionStoreError:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    if owner is None:
        return {"ok": True, "notified": False}

    try:
        status = state.accounts.client_for(owner).check_invoice_status(payment_hash)
    except LNbitsAPIError:
        raise HTTPException(status_code=502, detail="Could not verify payment")
    if not status.get("paid"):
        return {"ok": True, "notified": False}

    amount = status.get("amount") or 0
    state.accounts.forget_invoice(payment_hash)
    log_audit_payment(
        logger,
        operation="invoice_paid",
        phone_number=owner.phone_number,
        amount=amount,
        payment_hash=payment_hash,
        success=True,
    )
    if state.notifier is not None:
        message = tr(owner.language, "You received {amount} sats.", amount=f"{amount:,}")
        if cfg.ussd_code:
            message += " " + tr(owner.language, "Dial {code} to use it.", code=cfg.ussd_code)
        state.notifier.send(owner.phone_number, message)
    return {"ok": True, "notified": True}
