"""Rule-based extraction of candidate facts from message text.

Everything returned is a *candidate* with evidence. Nothing here decides that
a payment happened or that a booking is confirmed; the planner turns facts
into proposals a person reviews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .parser import Message

ROME = ZoneInfo("Europe/Rome")

_NUM = r"(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_AMOUNT = re.compile(rf"(?:€|\bEUR\b)\s?{_NUM}|{_NUM}\s?(?:€|\bEUR\b|\beuros?\b)", re.I)
_PAX = re.compile(r"\b(\d{1,2})\s?(?:pax|people|persons|passengers|personnes|persone|adults|guests)\b", re.I)
_FLIGHT = re.compile(r"\b(?:flight|vol|volo)\s*(?:no\.?|number|#)?\s*([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?(\d{2,4})\b", re.I)
_PAID = re.compile(
    r"\b(paid|sent|transferred|wired|deposit (?:is )?(?:sent|done|paid)|payment (?:sent|done|made)|"
    r"pagato|inviato|bonifico|payé|envoyé|virement)\b",
    re.I,
)
_DMY = re.compile(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b")
_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january", "gennaio", "janvier"),
            ("feb", "february", "febbraio", "février", "fevrier"),
            ("mar", "march", "marzo", "mars"),
            ("apr", "april", "aprile", "avril"),
            ("may", "maggio", "mai"),
            ("jun", "june", "giugno", "juin"),
            ("jul", "july", "luglio", "juillet"),
            ("aug", "august", "agosto", "août", "aout"),
            ("sep", "sept", "september", "settembre", "septembre"),
            ("oct", "october", "ottobre", "octobre"),
            ("nov", "november", "novembre"),
            ("dec", "december", "dicembre", "décembre", "decembre"),
        ]
    )
    for m in names
}
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?", re.I)
_MONTH_DAY = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)


@dataclass(frozen=True)
class Fact:
    kind: str  # amount | payment_reported | pax | flight | date_mentioned
    value: object
    message: Message
    snippet: str


def _amount(raw: str) -> float | None:
    raw = raw.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):  # 1.770 / 1,770 = thousands
        raw = re.sub(r"[.,]", "", raw)
    else:
        raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0 < value < 100000 else None


def _next_date(day: int, month: int, sent: date, year: int | None = None) -> date | None:
    try:
        if year is not None:
            return date(year if year > 100 else 2000 + year, month, day)
        candidate = date(sent.year, month, day)
    except ValueError:
        return None
    # A date without a year means the next occurrence on or after ~2 weeks before the message.
    if (sent - candidate).days > 14:
        try:
            candidate = date(sent.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def _snippet(text: str, start: int, end: int, width: int = 40) -> str:
    return text[max(0, start - width) : min(len(text), end + width)].replace("\n", " ").strip()


def extract_facts(message: Message) -> list[Fact]:
    text = message.text
    sent_local = message.timestamp_utc.astimezone(ROME).date()
    facts: list[Fact] = []
    paid = bool(_PAID.search(text))
    for m in _AMOUNT.finditer(text):
        value = _amount(m.group(1) or m.group(2))
        if value is not None:
            kind = "payment_reported" if paid else "amount"
            facts.append(Fact(kind, value, message, _snippet(text, m.start(), m.end())))
    for m in _PAX.finditer(text):
        facts.append(Fact("pax", int(m.group(1)), message, _snippet(text, m.start(), m.end())))
    for m in _FLIGHT.finditer(text):
        facts.append(Fact("flight", (m.group(1) + m.group(2)).upper(), message, _snippet(text, m.start(), m.end())))
    seen_dates: set[date] = set()
    for m in _DMY.finditer(text):
        d = _next_date(int(m.group(1)), int(m.group(2)), sent_local, int(m.group(3)) if m.group(3) else None)
        if d and d not in seen_dates:
            seen_dates.add(d)
            facts.append(Fact("date_mentioned", d, message, _snippet(text, m.start(), m.end())))
    for regex, day_idx, mon_idx in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        for m in regex.finditer(text):
            d = _next_date(int(m.group(day_idx)), _MONTHS[m.group(mon_idx).lower().rstrip(".")], sent_local)
            if d and d not in seen_dates:
                seen_dates.add(d)
                facts.append(Fact("date_mentioned", d, message, _snippet(text, m.start(), m.end())))
    return facts


def message_local_date(message: Message) -> date:
    return message.timestamp_utc.astimezone(ROME).date()


def iso_rome(ts: datetime) -> str:
    return ts.astimezone(ROME).isoformat(timespec="minutes")
