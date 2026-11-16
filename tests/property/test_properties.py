"""Property-based tests (Hypothesis) for validation, formatting and privacy."""

import re
from datetime import datetime

import pytest
from hypothesis import given, settings, strategies as st

from src.i18n import LANGUAGES, tr
from src.menu_handler import (
    MAX_MENU_CHARS,
    MenuHandler,
    format_balance,
    format_error_menu,
    validate_amount,
)
from src.models import Invoice, MenuState, PaymentResult, Transaction
from src.phone import mask_phone, normalize_phone

LANGS = st.sampled_from([code for code, _ in LANGUAGES])
local_numbers = st.from_regex(r"[1-9][0-9]{8}", fullmatch=True)


@pytest.mark.property
@given(local_numbers)
def test_local_numbers_get_country_code(number):
    assert normalize_phone(number, "221") == f"+221{number}"
    assert normalize_phone("0" + number, "221") == f"+221{number}"


@pytest.mark.property
@given(st.text(max_size=30))
def test_normalized_phone_is_always_international(raw):
    result = normalize_phone(raw, "221")
    assert result is None or re.fullmatch(r"\+\d{10,15}", result)


@pytest.mark.property
@given(st.from_regex(r"\+[0-9]{10,15}", fullmatch=True))
def test_mask_hides_the_middle(phone):
    masked = mask_phone(phone)
    assert len(masked) == len(phone)
    assert masked[:4] == phone[:4] and masked[-2:] == phone[-2:]
    assert set(masked[4:-2]) == {"*"}


@pytest.mark.property
@given(st.integers(min_value=1, max_value=99_999_999))
def test_valid_amounts_roundtrip(amount):
    result = validate_amount(str(amount))
    assert result.valid and result.parsed_value == amount


@pytest.mark.property
@given(st.integers(max_value=0) | st.integers(min_value=100_000_000))
def test_out_of_range_amounts_rejected(amount):
    assert not validate_amount(str(amount)).valid


@pytest.mark.property
@given(st.integers(min_value=0, max_value=21_000_000 * 10**8), LANGS)
def test_balance_keeps_exact_value(balance, lang):
    text = format_balance(balance, lang)
    digits = re.search(r"[\d,]+", text).group().replace(",", "")
    assert int(digits) == balance


@pytest.mark.property
@given(st.text(max_size=400), LANGS)
def test_error_screens_fit(message, lang):
    assert len(format_error_menu(message, lang)) <= MAX_MENU_CHARS


@pytest.mark.property
@given(st.text(min_size=1, max_size=50).filter(lambda s: "{" not in s and "}" not in s))
def test_unknown_language_falls_back_to_english(text):
    assert tr("xx", text) == text


transactions = st.lists(
    st.builds(
        Transaction,
        payment_hash=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
        amount=st.integers(min_value=-10**8, max_value=10**8),
        timestamp=st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2030, 1, 1)),
        memo=st.text(max_size=40),
        transaction_type=st.sampled_from(["sent", "received"]),
    ),
    max_size=5,
)

invoices = st.builds(
    Invoice,
    payment_hash=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    payment_request=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=4, max_size=600).map(lambda s: "lnbc" + s),
    amount=st.integers(min_value=1, max_value=99_999_999),
    memo=st.just("USSD Payment"),
)

screen_data = st.fixed_dictionaries({
    "balance_sats": st.integers(min_value=0, max_value=21 * 10**14),
    "transactions": transactions,
    "invoice": invoices,
    "sent_by_sms": st.booleans(),
    "paid": st.booleans(),
    "amount": st.integers(min_value=1, max_value=99_999_999),
    "recipient": st.from_regex(r"\+[0-9]{10,15}", fullmatch=True),
    "phone": st.from_regex(r"\+[0-9]{10,15}", fullmatch=True),
    "payment_result": st.builds(
        PaymentResult,
        success=st.booleans(),
        amount=st.none() | st.integers(min_value=1, max_value=99_999_999),
        error_message=st.none() | st.text(max_size=300),
    ),
    "page": st.integers(min_value=0, max_value=5),
})


@pytest.mark.property
@settings(max_examples=60)
@given(st.sampled_from(list(MenuState)), screen_data, LANGS)
def test_every_screen_fits_160_chars(state, data, lang):
    handler = MenuHandler(accounts=object(), lightning_domain="pay.example.com")
    assert len(handler.format_menu_text(state, data, lang)) <= MAX_MENU_CHARS
