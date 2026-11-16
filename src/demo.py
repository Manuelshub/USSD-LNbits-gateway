"""Demo routes: a web feature-phone simulator and the data it needs.

Disabled unless DEMO_MODE=true (all routes then return 404). Never exposes
admin_key or invoice_key. The simulator talks to the same state machine as
Africa's Talking, through /demo/ussd, so it works without the callback secret.
"""

import io
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from src.lnbits_client import LNbitsAPIError, LNbitsClient
from src.logging_config import get_logger
from src.models import Invoice
from src.i18n import tr
from src.phone import phone_to_username
from src.session_manager import SessionStoreError
from src.world_wallet import WorldWallet, WorldWalletError

logger = get_logger(__name__)

_PAGE = Path(__file__).parent / "demo_page.html"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PHONE_RE = re.compile(r"^\+\d{10,15}$")
# Most the faucet hands out per request, from the operator's wallet.
_FAUCET_MAX_SATS = 10_000


def _require_demo_mode() -> None:
    if os.getenv("DEMO_MODE", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/demo", dependencies=[Depends(_require_demo_mode)])


def _app_state():
    # Imported lazily: src.main includes this router, so a top-level import
    # would be circular.
    from src import main

    return main


@router.get("", response_class=HTMLResponse)
def demo_page() -> str:
    return _PAGE.read_text(encoding="utf-8")


def _check_phone(phone: str) -> str:
    if not _PHONE_RE.match(phone):
        raise HTTPException(status_code=422, detail="Phone must look like +221771234567")
    return phone


def _operator_client(cfg) -> LNbitsClient:
    return LNbitsClient(
        base_url=cfg.lnbits_url,
        admin_key=cfg.lnbits_admin_key,
        invoice_key=cfg.lnbits_invoice_key,
    )


@router.post("/ussd", response_class=PlainTextResponse)
def demo_ussd(
    sessionId: str = Form(...),
    serviceCode: str = Form(...),
    phoneNumber: str = Form(...),
    text: str = Form(default=""),
) -> str:
    """Same as /ussd, minus the callback secret (demo mode only)."""
    return _app_state().handle_ussd(sessionId, serviceCode, phoneNumber, text)


@router.get("/api/wallet")
def wallet(phone: str = Query(...)) -> dict:
    """Balance, Lightning address and last transactions of a phone's wallet."""
    _check_phone(phone)
    state = _app_state()
    if state.config is None or state.accounts is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")
    try:
        wallet_config = state.accounts.get(phone)
    except SessionStoreError:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    if wallet_config is None:
        return {"registered": False}

    client = state.accounts.client_for(wallet_config)
    try:
        balance = client.get_balance(wallet_config.wallet_id)
        txs = client.get_transactions(wallet_config.wallet_id, limit=5)
    except LNbitsAPIError as e:
        raise HTTPException(status_code=502, detail=e.message)

    domain = urlparse(state.config.public_url).netloc if state.config.public_url else ""
    return {
        "registered": True,
        "balance_sats": balance,
        "wallet_id_masked": wallet_config.wallet_id[:8] + "...",
        "lightning_address": f"{phone_to_username(phone)}@{domain}" if domain else "",
        "language": wallet_config.language,
        "transactions": [t.to_dict() for t in txs],
    }


@router.get("/api/sms")
def sms_inbox(phone: str = Query(...)) -> dict:
    """SMS the gateway sent to this phone (kept only in demo mode)."""
    _check_phone(phone)
    state = _app_state()
    if state.notifier is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")
    try:
        return {"messages": state.notifier.demo_inbox(phone)}
    except Exception:
        raise HTTPException(status_code=503, detail="Session store unavailable")


@router.post("/api/faucet")
def faucet(phone: str = Form(...), amount: int = Form(5000)) -> dict:
    """Send demo sats from the operator wallet to a registered phone."""
    _check_phone(phone)
    if not 1 <= amount <= _FAUCET_MAX_SATS:
        raise HTTPException(status_code=422, detail=f"Amount must be 1-{_FAUCET_MAX_SATS} sats")
    state = _app_state()
    if state.config is None or state.accounts is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")
    wallet_config = state.accounts.get(phone)
    if wallet_config is None:
        raise HTTPException(status_code=404, detail="Dial the code first to create this wallet")

    try:
        invoice = state.accounts.client_for(wallet_config).create_invoice(
            wallet_config.wallet_id, amount, memo="Demo faucet"
        )
        result = _operator_client(state.config).pay_invoice(
            state.config.lnbits_wallet_id, invoice.payment_request
        )
    except LNbitsAPIError as e:
        raise HTTPException(status_code=502, detail=e.message)
    if not result.success:
        raise HTTPException(
            status_code=409,
            detail=f"Faucet payment failed ({result.error_message}). Fund the operator wallet first.",
        )
    logger.info(
        f"Demo faucet sent {amount} sats",
        extra={'event_type': 'demo_faucet', 'phone_number': phone}
    )
    if state.notifier is not None:
        state.notifier.send(
            phone, tr(wallet_config.language, "You received {amount} sats.", amount=f"{amount:,}")
        )
    return {"ok": True, "amount": amount}


def _world() -> WorldWallet:
    world = WorldWallet.from_env()
    if world is None:
        raise HTTPException(status_code=404, detail="No outside-world node (run the regtest stack)")
    return world


@router.get("/api/world")
def world_info() -> dict:
    """The outside-world Lightning node: alias, balance, channels."""
    world = WorldWallet.from_env()
    if world is None:
        return {"available": False}
    try:
        return {"available": True, **world.info()}
    except WorldWalletError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/api/world/pay")
def world_pay(phone: str = Form(...), amount: int = Form(...)) -> dict:
    """Pay a phone's Lightning address from the outside world (LNURL-pay)."""
    _check_phone(phone)
    if not 1 <= amount <= 1_000_000:
        raise HTTPException(status_code=422, detail="Amount must be 1-1,000,000 sats")
    try:
        result = _world().pay_address(phone_to_username(phone), amount)
    except WorldWalletError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "address": result.address,
        "amount_sats": result.amount_sats,
        "fee_sats": result.fee_sats,
        "elapsed_ms": result.elapsed_ms,
        "payment_hash": result.payment_hash,
        "steps": result.steps,
    }


@router.post("/api/world/invoice")
def world_invoice(amount: int = Form(...)) -> dict:
    """Invoice from the outside world, for a USSD user to pay (cash out)."""
    if not 1 <= amount <= 1_000_000:
        raise HTTPException(status_code=422, detail="Amount must be 1-1,000,000 sats")
    try:
        return {"bolt11": _world().create_invoice(amount, "Cash out from USSD wallet")}
    except WorldWalletError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/api/session/{session_id}/invoice")
def session_invoice(session_id: str) -> dict:
    """Full invoice from a live session (the USSD screen truncates it)."""
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=422, detail="Invalid session id")
    sm = _app_state().session_manager
    if sm is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")
    try:
        session = sm.get_session(session_id)
    except SessionStoreError:
        raise HTTPException(status_code=503, detail="Session store unavailable")
    invoice = session.state_data.get("invoice") if session else None
    if not isinstance(invoice, Invoice):
        raise HTTPException(status_code=404, detail="No invoice in session")
    return invoice.to_dict()


@router.get("/api/qr")
def qr(data: str = Query(..., min_length=1, max_length=2000)) -> Response:
    """QR code (SVG) for the given text."""
    import segno

    buf = io.BytesIO()
    segno.make(data, error="l").save(buf, kind="svg", scale=5, border=2)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")
