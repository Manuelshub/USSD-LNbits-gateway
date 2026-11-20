"""End-to-end USSD flows: registration, PINs, send to phone, receive, settings.

Runs the real state machine, SessionManager and AccountService over an
in-memory Redis; only LNbits is mocked (one mock client per wallet).
"""

import itertools
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src import main
from src.accounts import AccountService
from src.config import Config
from src.lnbits_client import LNbitsAPIError
from src.menu_handler import MAX_MENU_CHARS
from src.models import PaymentResult
from src.notifier import SMSNotifier
from src.session_manager import SessionManager
from tests.fakes import FakeRedis, fake_lnbits_client

ALICE = "+221771111111"
BOB = "+221772222222"


def make_config(**overrides):
    values = dict(
        lnbits_url="http://lnbits:5000",
        lnbits_wallet_id="operator",
        lnbits_admin_key="operator-admin",
        lnbits_invoice_key="operator-inv",
        at_username="sandbox",
        at_api_key="",
        redis_url="redis://redis:6379/0",
        session_timeout=90,
        log_level="INFO",
        public_url="https://pay.example.com",
        default_country_code="221",
        webhook_secret="hook-secret",
        ussd_code="*384*96#",
    )
    values.update(overrides)
    return Config(**values)


class Gateway:
    """Test harness: patched app globals plus helpers to dial and type."""

    def __init__(self, config):
        self.redis = FakeRedis()
        self.session_manager = SessionManager(self.redis, timeout_seconds=90)
        self.accounts = AccountService(self.session_manager, lnbits_url=config.lnbits_url)
        self.notifier = SMSNotifier(redis_client=self.redis, demo_mode=True)
        self.config = config
        self.clients = {}  # admin_key -> mock LNbits client
        self._ids = itertools.count(1)
        self._sessions = itertools.count(1)

    def client_for_key(self, base_url, admin_key, invoice_key):
        return self.clients.setdefault(admin_key, fake_lnbits_client())

    def create_account(self, base_url, name):
        n = next(self._ids)
        return {"wallet_id": f"w{n}", "admin_key": f"admin{n}", "invoice_key": f"inv{n}"}

    def dial(self, phone, *steps):
        """One USSD session: dial, then type each step. Returns all screens."""
        session_id = f"s{next(self._sessions)}"
        screens = [main.handle_ussd(session_id, "*384*96#", phone, "")]
        typed = []
        for step in steps:
            typed.append(step)
            screens.append(main.handle_ussd(session_id, "*384*96#", phone, "*".join(typed)))
        return screens

    def register(self, phone, pin="4826", language="1"):
        return self.dial(phone, language, pin, pin)

    def sms(self, phone):
        return [m["message"] for m in self.notifier.demo_inbox(phone)]

    def client_of(self, phone):
        wallet = self.accounts.get(phone)
        return self.client_for_key(None, wallet.admin_key, wallet.invoice_key)


@pytest.fixture
def gw():
    gateway = Gateway(make_config())
    patches = [
        patch.object(main, "config", gateway.config),
        patch.object(main, "redis_client", gateway.redis),
        patch.object(main, "session_manager", gateway.session_manager),
        patch.object(main, "accounts", gateway.accounts),
        patch.object(main, "notifier", gateway.notifier),
        patch("src.main.LNbitsClient", side_effect=gateway.client_for_key),
        patch("src.accounts.LNbitsClient", side_effect=gateway.client_for_key),
        patch("src.accounts.LNbitsClient.create_account", side_effect=gateway.create_account),
    ]
    for p in patches:
        p.start()
    try:
        yield gateway
    finally:
        for p in patches:
            p.stop()


def body(screen):
    return screen.split(" ", 1)[1]


@pytest.mark.integration
class TestRegistration:
    def test_unknown_phone_sees_language_choice(self, gw):
        first = gw.dial(ALICE)[0]
        assert first.startswith("CON ")
        assert "1. English" in first and "2. Francais" in first

    def test_full_registration(self, gw):
        screens = gw.register(ALICE)
        assert "Create a 4-digit PIN" in screens[1]
        assert "Re-enter" in screens[2]
        assert "Wallet created!" in screens[3]
        assert "221771111111@pay.example.com" in screens[3]
        wallet = gw.accounts.get(ALICE)
        assert wallet.wallet_id == "w1" and wallet.pin_hash

        # Next dial goes straight to the main menu.
        assert "1. Check Balance" in gw.dial(ALICE)[0]

    def test_weak_pin_rejected(self, gw):
        screens = gw.dial(ALICE, "1", "1111")
        assert "PIN too easy to guess" in screens[2]
        assert gw.accounts.get(ALICE) is None

    def test_mismatched_pins_restart(self, gw):
        screens = gw.dial(ALICE, "1", "4826", "4827")
        assert "PINs do not match" in screens[3]
        assert gw.accounts.get(ALICE) is None

    def test_french_registration_and_menu(self, gw):
        screens = gw.register(ALICE, language="2")
        assert "Créez un PIN" in screens[1]
        assert "Portefeuille créé!" in screens[3]
        assert "Voir le solde" in gw.dial(ALICE)[0]

    def test_zero_exits_registration(self, gw):
        screens = gw.dial(ALICE, "0")
        assert screens[1].startswith("END ")

    def test_closed_registration(self, gw):
        gw.config.allow_registration = False
        assert gw.dial(ALICE)[0] == "END Registration is closed."

    def test_no_shared_wallet_fallback(self, gw):
        # An unknown phone never reaches a balance screen.
        screens = gw.dial(BOB, "1")
        assert "Balance" not in screens[1]
        assert gw.clients == {}


@pytest.mark.integration
class TestSendToPhone:
    def test_send_with_pin(self, gw):
        gw.register(ALICE)
        gw.register(BOB)
        screens = gw.dial(ALICE, "3", "1", "772222222", "500", "4826")
        assert "1. Phone number" in screens[1]
        assert "Amount to send" in screens[3]
        assert f"Send 500 sats to {BOB}?" in screens[4]
        assert "Enter PIN" in screens[4]
        assert f"Sent 500 sats to {BOB}" in screens[5]

        # Invoice created on Bob's wallet, paid from Alice's.
        gw.client_of(BOB).create_invoice.assert_called_once_with("w2", 500, memo="USSD transfer")
        pay = gw.client_of(ALICE).pay_invoice
        pay.assert_called_once()
        assert pay.call_args.args[0] == "w1"

        assert any("received 500 sats" in m and ALICE in m for m in gw.sms(BOB))

    def test_unregistered_recipient(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "3", "1", "779999999")
        assert "no wallet yet" in screens[3]

    def test_cannot_send_to_self(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "3", "1", "771111111")
        assert "cannot send to yourself" in screens[3]

    def test_wrong_pin_then_lockout(self, gw):
        gw.register(ALICE)
        gw.register(BOB)
        screens = gw.dial(ALICE, "3", "1", "772222222", "500", "0000", "0001", "0002")
        assert "Wrong PIN. Tries left: 2" in screens[5]
        assert "Wrong PIN. Tries left: 1" in screens[6]
        assert screens[7].startswith("END Too many wrong PINs")
        gw.client_of(ALICE).pay_invoice.assert_not_called()

        # Still locked in a new session, even with the right PIN.
        again = gw.dial(ALICE, "3", "1", "772222222", "500", "4826")
        assert again[5].startswith("END Too many wrong PINs")

    def test_insufficient_balance(self, gw):
        gw.register(ALICE)
        gw.register(BOB)
        gw.client_of(ALICE).pay_invoice.return_value = PaymentResult(
            success=False, error_message="Insufficient balance"
        )
        screens = gw.dial(ALICE, "3", "1", "772222222", "500", "4826")
        assert "Insufficient balance" in screens[5]
        assert "1:Retry" in screens[5]
        assert gw.sms(BOB) == []

    def test_amount_above_balance_rejected_before_pin(self, gw):
        gw.register(ALICE)
        gw.register(BOB)
        gw.client_of(ALICE).get_balance.return_value = 300
        screens = gw.dial(ALICE, "3", "1", "772222222", "500")
        assert "Insufficient balance" in screens[4]
        assert "Amount to send" in screens[4]
        assert "Enter PIN" not in screens[4]
        gw.client_of(ALICE).pay_invoice.assert_not_called()

    def test_balance_lookup_failure_still_asks_for_pin(self, gw):
        gw.register(ALICE)
        gw.register(BOB)
        gw.client_of(ALICE).get_balance.side_effect = LNbitsAPIError("down", "CONNECTION_ERROR")
        screens = gw.dial(ALICE, "3", "1", "772222222", "500")
        assert "Enter PIN" in screens[4]


@pytest.mark.integration
class TestPayInvoiceWithPin:
    def test_invoice_payment_requires_pin(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "3", "2", "lnbc20u1pexample", "4826")
        assert "Pay 2,000 sats?" in screens[3]
        assert "Enter PIN" in screens[3]
        assert "Sent" in screens[4] or "Payment sent" in screens[4]
        gw.client_of(ALICE).pay_invoice.assert_called_once_with("w1", "lnbc20u1pexample")

    def test_rejected_invoice_reprompts(self, gw):
        gw.register(ALICE)
        gw.client_of(ALICE).decode_invoice.side_effect = LNbitsAPIError("Service unavailable", "HTTP_ERROR")
        screens = gw.dial(ALICE, "3", "2", "lnbcgarbage")
        assert "Invalid invoice." in screens[3]
        assert "Enter invoice to pay" in screens[3]
        assert "unavailable" not in screens[3]

    def test_lnbits_down_on_decode_is_a_service_error(self, gw):
        gw.register(ALICE)
        gw.client_of(ALICE).decode_invoice.side_effect = LNbitsAPIError(
            "LNbits service unavailable", "CONNECTION_ERROR"
        )
        screens = gw.dial(ALICE, "3", "2", "lnbc20u1pexample")
        assert "unavailable" in screens[3]
        assert "1:Retry" in screens[3]

    def test_invoice_above_balance_rejected_before_pin(self, gw):
        gw.register(ALICE)
        gw.client_of(ALICE).get_balance.return_value = 1000  # invoice is 2,000
        screens = gw.dial(ALICE, "3", "2", "lnbc20u1pexample")
        assert "Insufficient balance" in screens[3]
        assert "Enter invoice to pay" in screens[3]
        gw.client_of(ALICE).pay_invoice.assert_not_called()


@pytest.mark.integration
class TestReceive:
    def test_receive_shows_lightning_address(self, gw):
        gw.register(ALICE)
        screen = gw.dial(ALICE, "2")[1]
        assert "221771111111@pay.example.com" in screen

    def test_invoice_sent_by_sms_with_webhook(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "2", "1", "1000")
        assert "sent to you by SMS" in screens[3]
        create = gw.client_of(ALICE).create_invoice
        create.assert_called_once_with(
            "w1", 1000, webhook="http://gateway:8000/webhooks/lnbits/hook-secret"
        )
        bolt11 = create.return_value.payment_request
        assert any(bolt11 in m for m in gw.sms(ALICE))
        assert gw.accounts.invoice_owner("f" * 64).phone_number == ALICE

    def test_status_check_after_sms(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "2", "1", "1000", "1")
        assert "Paid: 1,000 sats" in screens[4]


@pytest.mark.integration
class TestSettings:
    def test_switch_language(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "5", "1", "2")
        assert "Langue modifiée." in screens[3]
        assert gw.accounts.get(ALICE).language == "fr"

    def test_change_pin(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "5", "2", "4826", "9361", "9361")
        assert "PIN changed." in screens[5]
        wallet = gw.accounts.get(ALICE)
        assert gw.accounts.verify_pin(wallet, "9361").ok

    def test_change_pin_needs_current_pin(self, gw):
        gw.register(ALICE)
        screens = gw.dial(ALICE, "5", "2", "0000")
        assert "Wrong PIN" in screens[3]


@pytest.mark.integration
class TestScreensFit:
    @pytest.mark.parametrize("language", ["1", "2"])
    def test_every_screen_fits_160_chars(self, gw, language):
        gw.register(ALICE, language=language)
        gw.register(BOB)
        sessions = [
            gw.register("+221773333333", language=language),
            gw.dial(ALICE, "1"),
            gw.dial(ALICE, "2", "1", "1000", "1"),
            gw.dial(ALICE, "3", "1", "772222222", "500", "0000", "4826"),
            gw.dial(ALICE, "3", "2", "lnbc20u1pexample", "4826"),
            gw.dial(ALICE, "4"),
            gw.dial(ALICE, "5", "2", "4826", "1111", "9361", "9362"),
            gw.dial(ALICE, "6"),
        ]
        for screens in sessions:
            for screen in screens:
                assert len(body(screen)) <= MAX_MENU_CHARS, screen


@pytest.mark.integration
class TestSecurityEndpoints:
    def test_callback_secret_enforced(self, gw):
        gw.config.ussd_callback_secret = "s3cret"
        client = TestClient(main.app)
        form = {"sessionId": "x1", "serviceCode": "*384*96#", "phoneNumber": ALICE, "text": ""}
        assert client.post("/ussd", data=form).status_code == 403
        assert client.post("/ussd?key=wrong", data=form).status_code == 403
        assert client.post("/ussd/wrong", data=form).status_code == 403
        assert client.post("/ussd/s3cret", data=form).status_code == 200
        ok = client.post("/ussd?key=s3cret", data=form)
        assert ok.status_code == 200 and ok.text.startswith("CON ")

    def test_pin_and_phone_not_in_logs(self, gw, caplog):
        import logging
        from src.logging_config import StructuredFormatter

        gw.register(BOB)
        with caplog.at_level(logging.INFO):
            gw.register(ALICE, pin="5937")
            gw.dial(ALICE, "3", "1", "772222222", "500", "5937")
        formatter = StructuredFormatter()
        output = "\n".join(formatter.format(r) for r in caplog.records)
        assert "5937" not in output
        assert "771111111" not in output and "772222222" not in output


@pytest.mark.integration
class TestLightningAddress:
    def test_lnurlp_and_callback(self, gw):
        gw.register(ALICE)
        client = TestClient(main.app)
        meta = client.get("/.well-known/lnurlp/221771111111").json()
        assert meta["tag"] == "payRequest"
        assert meta["callback"] == "https://pay.example.com/lnurlp/221771111111/callback"
        assert "221771111111@pay.example.com" in meta["metadata"]

        invoice = client.get(meta["callback"].replace("https://pay.example.com", ""),
                             params={"amount": 21000}).json()
        assert invoice["pr"].startswith("lnbc")
        create = gw.client_of(ALICE).create_invoice
        args, kwargs = create.call_args
        assert args == ("w1", 21)
        assert bytes.fromhex(kwargs["unhashed_description"]).decode() == meta["metadata"]
        assert kwargs["webhook"].endswith("/webhooks/lnbits/hook-secret")

    def test_unknown_address(self, gw):
        client = TestClient(main.app)
        r = client.get("/.well-known/lnurlp/221779999999")
        assert r.status_code == 404 and r.json()["status"] == "ERROR"

    def test_bad_amounts(self, gw):
        gw.register(ALICE)
        client = TestClient(main.app)
        for amount in (0, 1500, 10**13):
            r = client.get("/lnurlp/221771111111/callback", params={"amount": amount}).json()
            assert r["status"] == "ERROR"

    def test_disabled_without_public_url(self, gw):
        gw.config.public_url = ""
        gw.register(ALICE)
        client = TestClient(main.app)
        assert client.get("/.well-known/lnurlp/221771111111").status_code == 404


@pytest.mark.integration
class TestPaidWebhook:
    def test_notifies_owner_once(self, gw):
        gw.register(ALICE)
        gw.dial(ALICE, "2", "1", "1000")
        client = TestClient(main.app)
        url = "/webhooks/lnbits/hook-secret"
        first = client.post(url, json={"payment_hash": "f" * 64}).json()
        second = client.post(url, json={"payment_hash": "f" * 64}).json()
        assert first["notified"] is True and second["notified"] is False
        assert sum("received 1,000 sats" in m for m in gw.sms(ALICE)) == 1

    def test_accepts_lnbits_double_encoded_body(self, gw):
        import json
        gw.register(ALICE)
        gw.dial(ALICE, "2", "1", "1000")
        client = TestClient(main.app)
        r = client.post("/webhooks/lnbits/hook-secret", json=json.dumps({"payment_hash": "f" * 64}))
        assert r.json()["notified"] is True

    def test_wrong_secret(self, gw):
        client = TestClient(main.app)
        r = client.post("/webhooks/lnbits/nope", json={"payment_hash": "f" * 64})
        assert r.status_code == 404

    def test_unpaid_invoice_not_notified(self, gw):
        gw.register(ALICE)
        gw.dial(ALICE, "2", "1", "1000")
        gw.client_of(ALICE).check_invoice_status.return_value = {"paid": False, "amount": 1000}
        client = TestClient(main.app)
        r = client.post("/webhooks/lnbits/hook-secret", json={"payment_hash": "f" * 64}).json()
        assert r["notified"] is False
