"""Parse WhatsApp .txt chat exports (iOS and Android layouts).

Message text is kept as inert data. Nothing in a message is ever executed or
treated as an instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# iOS:     [08/10/2026, 07:35:12] Name: text
# Android: 08/10/2026, 07:35 - Name: text
_IOS = re.compile(
    r"^‎?\[(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4}),? (?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?\s?(?P<ampm>[AaPp][Mm])?\] (?P<rest>.*)$"
)
_ANDROID = re.compile(
    r"^‎?(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{2,4}),? (?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?\s?(?P<ampm>[AaPp][Mm])? - (?P<rest>.*)$"
)
_ATTACHMENT = re.compile(r"<attached: [^>]+>|\b(image|video|audio|document|sticker|GIF) omitted\b|\(file attached\)", re.I)


@dataclass(frozen=True)
class Message:
    timestamp_utc: datetime
    local_text: str  # original timestamp text, for evidence
    sender: str | None  # None for system lines ("Messages are end-to-end encrypted")
    text: str
    has_attachment: bool


def _to_utc(parts: dict[str, str], tz: ZoneInfo, day_first: bool) -> datetime:
    d, m = int(parts["d"]), int(parts["m"])
    if not day_first:
        d, m = m, d
    y = int(parts["y"])
    if y < 100:
        y += 2000
    hour = int(parts["H"])
    ampm = (parts.get("ampm") or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    local = datetime(y, m, d, hour, int(parts["M"]), int(parts.get("S") or 0), tzinfo=tz)
    # Ambiguous autumn-change times resolve to the first occurrence (fold=0);
    # the evidence keeps the original local text so a person can check.
    return local.astimezone(timezone.utc)


def parse_export(text: str, tz_name: str = "Europe/Rome", day_first: bool = True) -> list[Message]:
    """Parse an export. tz_name is the timezone of the phone that exported it."""
    tz = ZoneInfo(tz_name)
    messages: list[dict] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        match = _IOS.match(line) or _ANDROID.match(line)
        if not match:
            if messages:
                messages[-1]["text"] += "\n" + line
            continue
        parts = match.groupdict()
        rest = parts["rest"]
        sender, body = (rest.split(": ", 1) + [""])[:2] if ": " in rest else (None, rest)
        messages.append(
            {
                "timestamp_utc": _to_utc(parts, tz, day_first),
                "local_text": line[: match.start("rest")].strip(" -[]‎"),
                "sender": sender.strip("‎ ") if sender else None,
                "text": body,
            }
        )
    return [
        Message(
            timestamp_utc=m["timestamp_utc"],
            local_text=m["local_text"],
            sender=m["sender"],
            text=m["text"].strip(),
            has_attachment=bool(_ATTACHMENT.search(m["text"])),
        )
        for m in messages
    ]
