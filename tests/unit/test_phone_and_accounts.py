"""Phone normalization/masking, PIN hashing and the account service."""

from unittest.mock import patch

import pytest

from src.accounts import AccountService, hash_pin, pin_matches, validate_new_pin
from src.phone import mask_phone, normalize_phone, phone_to_username, username_to_phone
from src.session_manager import SessionManager
from tests.fakes import FakeRedis


@pytest.mark.unit
class TestNormalizePhone:
    @pytest.mark.parametrize("raw", [
        "+221771234567", "221771234567", "00221771234567", "771234567", "0771234567",
        " 77 123 45 67 ",
    ])
    def test_senegal_formats(self, raw):
        assert normalize_phone(raw, "221") == "+221771234567"

    def test_other_country_in_international_form(self):
        assert normalize_phone("+2348012345678", "221") == "+2348012345678"

    @pytest.mark.parametrize("raw", ["", "abc", "12", "+22177abc4567", "+1234567890123456"])
    def test_rejects_garbage(self, raw):
        assert normalize_phone(raw, "221") is None


@pytest.mark.unit
class TestMaskPhone:
    def test_keeps_country_code_and_last_two_digits(self):
        assert mask_phone("+221771234567") == "+221*******67"

    def test_hides_short_values_entirely(self):
        assert mask_phone("12345") == "*****"

    def test_username_roundtrip(self):
        assert username_to_phone(phone_to_username("+221771234567")) == "+221771234567"
        assert username_to_phone("alice") is None


@pytest.mark.unit
class TestPins:
    def test_hash_and_match(self):
        salt, digest = hash_pin("4826")
        assert pin_matches("4826", salt, digest)
        assert not pin_matches("4827", salt, digest)

    def test_salt_differs_per_hash(self):
        assert hash_pin("4826")[0] != hash_pin("4826")[0]

    def test_no_hash_never_matches(self):
        assert not pin_matches("4826", "", "")

    @pytest.mark.parametrize("pin, ok", [
        ("4826", True), ("1111", False), ("1234", False), ("123", False),
        ("12345", False), ("12a4", False), ("", False),
    ])
    def test_new_pin_rules(self, pin, ok):
        assert (validate_new_pin(pin) is None) == ok


@pytest.fixture
def accounts():
    sm = SessionManager(FakeRedis(), timeout_seconds=90)
    return AccountService(sm, lnbits_url="http://lnbits:5000", max_pin_attempts=3)


def _register(accounts, phone="+221771234567", pin="4826", wallet_id="w1"):
    salt, digest = hash_pin(pin)
    with patch("src.accounts.LNbitsClient.create_account", return_value={
        "wallet_id": wallet_id, "admin_key": f"admin-{wallet_id}", "invoice_key": f"inv-{wallet_id}",
    }) as create:
        wallet = accounts.register(phone, salt, digest, "fr")
    return wallet, create


@pytest.mark.unit
class TestAccountService:
    def test_register_creates_and_stores_wallet(self, accounts):
        wallet, create = _register(accounts)
        create.assert_called_once()
        stored = accounts.get("+221771234567")
        assert stored.wallet_id == "w1"
        assert stored.language == "fr"
        assert stored.pin_hash and "4826" not in stored.pin_hash

    def test_register_is_idempotent(self, accounts):
        _register(accounts)
        _, create = _register(accounts, wallet_id="w2")
        create.assert_not_called()
        assert accounts.get("+221771234567").wallet_id == "w1"

    def test_wrong_pins_lock_the_phone(self, accounts):
        wallet, _ = _register(accounts)
        first = accounts.verify_pin(wallet, "0000")
        assert not first.ok and first.attempts_left == 2
        accounts.verify_pin(wallet, "0000")
        third = accounts.verify_pin(wallet, "0000")
        assert third.locked
        # Even the right PIN is refused while locked.
        assert accounts.verify_pin(wallet, "4826").locked

    def test_right_pin_resets_failures(self, accounts):
        wallet, _ = _register(accounts)
        accounts.verify_pin(wallet, "0000")
        assert accounts.verify_pin(wallet, "4826").ok
        assert accounts.verify_pin(wallet, "0000").attempts_left == 2

    def test_invoice_owner_roundtrip(self, accounts):
        _register(accounts)
        accounts.remember_invoice("hash1", "+221771234567")
        assert accounts.invoice_owner("hash1").wallet_id == "w1"
        accounts.forget_invoice("hash1")
        assert accounts.invoice_owner("hash1") is None


@pytest.mark.unit
def test_access_log_filter_masks_phone_in_path():
    import logging
    from src.logging_config import AccessLogPrivacyFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/.well-known/lnurlp/221771234567?phone=%2B221771234567", "1.1", 200),
        None,
    )
    AccessLogPrivacyFilter().filter(record)
    line = record.getMessage()
    assert "771234567" not in line
    assert "/lnurlp/221*******67" in line


@pytest.mark.unit
@pytest.mark.parametrize("path", [
    "/ussd/1ad49eb20be9497f17ff28b8872afa7",
    "/ussd?key=1ad49eb20be9497f17ff28b8872afa7",
    "/webhooks/lnbits/1ad49eb20be9497f17ff28b8872afa7",
])
def test_access_log_filter_redacts_callback_secrets(path):
    import logging
    from src.logging_config import AccessLogPrivacyFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", path, "1.1", 200), None,
    )
    AccessLogPrivacyFilter().filter(record)
    line = record.getMessage()
    assert "1ad49" not in line and "afa7" not in line
    assert "<redacted>" in line
