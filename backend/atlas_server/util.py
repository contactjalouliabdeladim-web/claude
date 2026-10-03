"""Petits utilitaires : expurgation des secrets, dates en français."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:ghp|gho|github_pat|xox[abpr])_[A-Za-z0-9_]{8,}"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[=:]\s*\S+"),
    re.compile(r"\b[0-9]{8,10}:[A-Za-z0-9_-]{30,}\b"),  # jeton de bot Telegram
]


def redact(text: str | None) -> str | None:
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) + " ***") if m.lastindex else "***", text)
    return text


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def now_in(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo("UTC"))


def today_iso(tz_name: str) -> str:
    return now_in(tz_name).date().isoformat()


def french_datetime(tz_name: str) -> str:
    now = now_in(tz_name)
    return f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]} {now.year}, {now:%H:%M}"
