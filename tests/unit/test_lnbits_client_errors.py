"""LNbits error mapping and the webhook fallback, against LNbits 1.6 responses."""

from unittest.mock import Mock, patch

import pytest
import requests

from src.lnbits_client import LNbitsAPIError, LNbitsClient


def _response(status, body):
    response = Mock()
    response.status_code = status
    response.json.return_value = body
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    return response


@pytest.fixture
def client():
    return LNbitsClient("http://lnbits:5000", admin_key="admin", invoice_key="inv")


@pytest.mark.unit
class TestErrorMapping:
    def test_insufficient_balance_from_520_detail(self, client):
        with patch("src.lnbits_client.requests.request",
                   return_value=_response(520, {"detail": "Insufficient balance.", "status": "failed"})):
            result = client.pay_invoice("w1", "lnbc1...")
        assert not result.success
        assert result.error_message == "Insufficient balance"

    def test_http_error_message_never_contains_url(self, client):
        with patch("src.lnbits_client.requests.request",
                   return_value=_response(400, {"detail": "Invoice expired."})):
            with pytest.raises(LNbitsAPIError) as exc:
                client.decode_invoice("lnbc1...")
        assert exc.value.message == "Invoice expired"
        assert "http" not in exc.value.message

    def test_http_error_without_body(self, client):
        response = _response(500, None)
        response.json.side_effect = ValueError("no json")
        with patch("src.lnbits_client.requests.request", return_value=response):
            with pytest.raises(LNbitsAPIError) as exc:
                client.get_balance("w1")
        assert exc.value.message == "Service unavailable"


@pytest.mark.unit
class TestWebhookFallback:
    def test_retries_without_rejected_webhook(self, client):
        rejected = _response(400, {"detail": "Callback URL origin 'http://gateway:8000' is not allowed."})
        created = _response(201, {"payment_hash": "h" * 64, "bolt11": "lnbc1..."})
        with patch("src.lnbits_client.requests.request", side_effect=[rejected, created]) as request:
            invoice = client.create_invoice("w1", 1000, webhook="http://gateway:8000/webhooks/lnbits/x")
        assert invoice.payment_request == "lnbc1..."
        first, second = (call.kwargs["json"] for call in request.call_args_list)
        assert "webhook" in first and "webhook" not in second

    def test_create_account(self):
        body = {"id": "w9", "adminkey": "a9", "inkey": "i9", "user": "u9"}
        with patch("src.lnbits_client.requests.request", return_value=_response(200, body)) as request:
            account = LNbitsClient.create_account("http://lnbits:5000", "USSD +221***67")
        assert account == {"wallet_id": "w9", "admin_key": "a9", "invoice_key": "i9"}
        assert request.call_args.args[1] == "http://lnbits:5000/api/v1/account"


@pytest.mark.unit
def test_history_skips_unpaid_and_failed(client):
    records = [
        {"status": "pending", "amount": 5_000_000, "payment_hash": "a", "time": 1},
        {"status": "success", "amount": 2_000_000, "payment_hash": "b", "time": 2},
        {"status": "failed", "amount": -1_000_000, "payment_hash": "c", "time": 3},
        {"pending": False, "amount": -500_000, "payment_hash": "d", "time": 4},
    ]
    with patch("src.lnbits_client.requests.request", return_value=_response(200, records)) as request:
        txs = client.get_transactions("w1", limit=5)
    assert [t.payment_hash for t in txs] == ["b", "d"]
    assert request.call_args.kwargs["params"] == {"limit": 20}
