"""Regression: Session.state_data holds model objects and must survive real JSON."""

import json
from datetime import datetime

from src.models import Invoice, MenuState, PaymentResult, Session, Transaction


def _roundtrip(session: Session) -> Session:
    # Same path SessionManager uses: json.dumps(to_json()) -> Redis -> from_json
    return Session.from_json(json.loads(json.dumps(session.to_json())))


def _session(state_data):
    return Session(
        session_id="s1",
        phone_number="+221771234567",
        wallet_id="w1",
        current_state=MenuState.INVOICE_DISPLAY,
        state_data=state_data,
    )


def test_invoice_in_state_data_roundtrips():
    inv = Invoice("hash123", "lnbc1000n1xyz", 1000, "USSD Payment", datetime(2026, 1, 2, 3, 4, 5))
    out = _roundtrip(_session({"_page": 0, "invoice": inv}))
    assert out.state_data["invoice"] == inv
    assert out.state_data["_page"] == 0


def test_transactions_list_roundtrips():
    txs = [
        Transaction("h1", 500, datetime(2026, 1, 1), "a", "received"),
        Transaction("h2", -200, datetime(2026, 1, 2), "b", "sent"),
    ]
    out = _roundtrip(_session({"transactions": txs}))
    assert out.state_data["transactions"] == txs


def test_payment_result_roundtrips():
    pr = PaymentResult(success=True, payment_hash="h", amount=300, fee=1)
    out = _roundtrip(_session({"payment_result": pr}))
    assert out.state_data["payment_result"] == pr


def test_plain_values_unchanged():
    data = {"balance_sats": 42, "paid": False, "amount": None, "payment_request": "lnbc..."}
    assert _roundtrip(_session(data)).state_data == data
