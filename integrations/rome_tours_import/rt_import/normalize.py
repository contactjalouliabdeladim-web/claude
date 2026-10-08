"""Stable identifiers: phone numbers, chat keys, message fingerprints."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .parser import Message

_FILENAME_PHONE = re.compile(r"(\+?\d[\d\s().-]{6,}\d)")


def normalize_phone(value: str | None) -> str | None:
    """Return '+<digits>' or None. Never guesses a missing country code."""
    if not value:
        return None
    value = value.strip().replace("\u200e", "").replace("\u202a", "").replace("\u202c", "")
    digits = re.sub(r"\D", "", value)
    if value.startswith("00"):
        digits = digits[2:]
    elif not value.startswith("+"):
        return None
    return "+" + digits if 8 <= len(digits) <= 15 else None


def chat_key_from_filename(filename: str) -> str | None:
    """'WhatsApp Chat with +1 555 010 0001.txt' -> '+15550100001'."""
    match = _FILENAME_PHONE.search(filename)
    return normalize_phone(match.group(1)) if match else None


def _norm_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("‎", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(chat_key: str, message: Message) -> str:
    """Same message in a repeated or overlapping export -> same fingerprint."""
    payload = "|".join(
        [chat_key, message.timestamp_utc.isoformat(), message.sender or "", _norm_text(message.text)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
