"""Deterministic domain validators for Polish administrative forms."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
_REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)

_POSTAL_CODE_RE = re.compile(r"^\d{2}-\d{3}$")
_PHONE_CHARS_RE = re.compile(r"^\+?[\d\s()-]+$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y")


def _extract_pesel_birth_date(digits: str) -> date | None:
    year = int(digits[0:2])
    month_code = int(digits[2:4])
    day = int(digits[4:6])

    if 1 <= month_code <= 12:
        century, month = 1900, month_code
    elif 21 <= month_code <= 32:
        century, month = 2000, month_code - 20
    elif 41 <= month_code <= 52:
        century, month = 2100, month_code - 40
    elif 61 <= month_code <= 72:
        century, month = 2200, month_code - 60
    elif 81 <= month_code <= 92:
        century, month = 1800, month_code - 80
    else:
        return None

    try:
        return date(century + year, month, day)
    except ValueError:
        return None


def validate_pesel(value: str) -> bool:
    """Validate Polish PESEL number (11 digits, birth date, and checksum)."""
    if not isinstance(value, str):
        return False

    digits = value.strip()
    if len(digits) != 11 or not digits.isdigit():
        return False

    if _extract_pesel_birth_date(digits) is None:
        return False

    total = sum(int(d) * w for d, w in zip(digits[:10], _PESEL_WEIGHTS, strict=True))
    control = (10 - (total % 10)) % 10
    return control == int(digits[10])


def validate_nip(value: str) -> bool:
    """Validate Polish NIP tax identification number (10 digits and checksum)."""
    if not isinstance(value, str):
        return False

    digits = re.sub(r"[\s-]", "", value.strip())
    if len(digits) != 10 or not digits.isdigit() or digits == "0" * 10:
        return False

    checksum = sum(int(d) * w for d, w in zip(digits[:9], _NIP_WEIGHTS, strict=True)) % 11
    return checksum == int(digits[9])


def _check_regon_checksum(digits: str, weights: tuple[int, ...]) -> bool:
    checksum = sum(int(d) * w for d, w in zip(digits[:-1], weights, strict=True)) % 11
    control = 0 if checksum == 10 else checksum
    return control == int(digits[-1])


def validate_regon(value: str) -> bool:
    """Validate Polish REGON number (9 or 14 digits with checksum)."""
    if not isinstance(value, str):
        return False

    digits = re.sub(r"[\s-]", "", value.strip())
    if not digits.isdigit() or set(digits) == {"0"}:
        return False

    if len(digits) == 9:
        return _check_regon_checksum(digits, _REGON9_WEIGHTS)
    if len(digits) == 14:
        valid_prefix = _check_regon_checksum(digits[:9], _REGON9_WEIGHTS)
        return valid_prefix and _check_regon_checksum(digits, _REGON14_WEIGHTS)
    return False


def validate_postal_code(value: str) -> bool:
    """Validate Polish postal code in XX-XXX format."""
    if not isinstance(value, str):
        return False
    return bool(_POSTAL_CODE_RE.match(value.strip()))


def validate_phone(value: str) -> bool:
    """Validate Polish (9 digits) or international (+ / 00, 9-15 digits) phone."""
    if not isinstance(value, str):
        return False

    text = value.strip()
    if not text or not _PHONE_CHARS_RE.match(text):
        return False
    if text.count("(") != text.count(")"):
        return False

    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        return 9 <= len(digits) <= 15
    if text.startswith("00"):
        return 9 <= len(digits[2:]) <= 15
    return len(digits) == 9


def validate_email(value: str) -> bool:
    """Validate an e-mail address with basic RFC structural checks."""
    if not isinstance(value, str):
        return False

    text = value.strip()
    if not text or len(text) > 254 or ".." in text:
        return False
    if not _EMAIL_RE.match(text):
        return False

    local, domain = text.split("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith("."):
        return False

    labels = domain.split(".")
    if any(label.startswith("-") or label.endswith("-") for label in labels):
        return False
    tld = labels[-1]
    return len(tld) >= 2 and tld.isalpha()


def validate_date(value: str) -> bool:
    """Validate a calendar date in YYYY-MM-DD, DD-MM-YYYY, DD.MM.YYYY, or DD/MM/YYYY."""
    if not isinstance(value, str):
        return False

    text = value.strip()
    if len(text) != 10:
        return False

    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "pesel": validate_pesel,
    "nip": validate_nip,
    "regon": validate_regon,
    "postal_code": validate_postal_code,
    "phone": validate_phone,
    "email": validate_email,
    "date": validate_date,
}


def validate_value(rule_name: str, value: str) -> bool:
    """Run a registered validator by rule name."""
    if rule_name not in VALIDATORS:
        raise KeyError(f"Unknown validator rule: {rule_name}")
    return VALIDATORS[rule_name](value)
