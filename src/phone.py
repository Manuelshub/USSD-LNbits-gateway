"""Phone number helpers: normalization of keypad input and masking for logs."""

import re
from typing import Optional

_DIGITS_RE = re.compile(r"^\d+$")


def normalize_phone(raw: str, country_code: str) -> Optional[str]:
    """Turn a number typed on a keypad into international ``+<digits>`` form.

    Accepted inputs (with ``country_code="221"``):

    * ``+221771234567`` / ``221771234567`` / ``00221771234567``
    * ``0771234567`` (trunk prefix ``0`` is dropped)
    * ``771234567`` (local number, country code is prepended)

    Returns None when the input cannot be a phone number.
    """
    if raw is None:
        return None
    text = raw.strip().replace(" ", "")
    if text.startswith("+"):
        digits = text[1:]
    elif text.startswith("00"):
        digits = text[2:]
    else:
        digits = text
        # National numbers have at least 8 digits, so a number that starts
        # with the country code and is long enough already includes it.
        if not (digits.startswith(country_code) and len(digits) >= len(country_code) + 8):
            digits = country_code + digits.lstrip("0")

    if not _DIGITS_RE.match(digits or ""):
        return None
    if not 10 <= len(digits) <= 15:
        return None
    return f"+{digits}"


def phone_to_username(phone_number: str) -> str:
    """Lightning address username for a phone number: its digits only."""
    return phone_number.lstrip("+")


def username_to_phone(username: str) -> Optional[str]:
    """Inverse of :func:`phone_to_username`; None when not a valid username."""
    if not _DIGITS_RE.match(username or "") or not 10 <= len(username) <= 15:
        return None
    return f"+{username}"


def mask_phone(phone_number: str) -> str:
    """Mask a phone number for logs: ``+221771234567`` -> ``+221*******67``."""
    if not phone_number:
        return ""
    value = str(phone_number)
    prefix = "+" if value.startswith("+") else ""
    digits = value.lstrip("+")
    if len(digits) <= 5:
        return prefix + "*" * len(digits)
    return f"{prefix}{digits[:3]}{'*' * (len(digits) - 5)}{digits[-2:]}"
