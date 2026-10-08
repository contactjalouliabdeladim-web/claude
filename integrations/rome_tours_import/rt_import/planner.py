"""Turn parsed messages + a records snapshot into a reviewable proposal.

Rules (from the operations brief):
- Match on the stable chat key (phone) and Source key, never on a name alone.
- Never overwrite a non-empty field with a different value: that is a conflict.
- A client saying "paid" creates at most a payment *reported by client*.
  Verification is a separate human step.
- Re-running the same or an overlapping export must not duplicate anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .extract import Fact, extract_facts, iso_rome, message_local_date
from .normalize import fingerprint, normalize_phone
from .parser import Message

PAYMENT_DEDUP_WINDOW_DAYS = 3


@dataclass
class Proposal:
    action: str  # create | update | conflict | skip
    entity: str  # prospect | service | booking | payment
    target_id: str | None
    fields: dict
    reason: str
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "entity": self.entity,
            "target_id": self.target_id,
            "fields": self.fields,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class Plan:
    chat_key: str
    new_messages: int
    skipped_already_seen: int
    proposals: list[Proposal]
    new_watermark_utc: str | None
    new_fingerprints: list[str]

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for p in self.proposals:
            out[p.action] = out.get(p.action, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {
            "chat_key": self.chat_key,
            "new_messages": self.new_messages,
            "skipped_already_seen": self.skipped_already_seen,
            "counts": self.counts(),
            "proposals": [p.as_dict() for p in self.proposals],
            "batch_update": {
                "new_watermark_utc": self.new_watermark_utc,
                "fingerprints_added": len(self.new_fingerprints),
            },
        }


def _evidence(fact: Fact) -> str:
    who = fact.message.sender or "system"
    return f"{iso_rome(fact.message.timestamp_utc)} {who}: “{fact.snippet}”"


def _records_for(snapshot: dict, kind: str, chat_key: str) -> list[dict]:
    return [r for r in snapshot.get(kind, []) if normalize_phone(r.get("phone")) == chat_key]


def _compare(proposals: list[Proposal], entity: str, record: dict, field_name: str, value, fact: Fact) -> None:
    current = record.get(field_name)
    if current in (None, "", []):
        proposals.append(
            Proposal("update", entity, record["id"], {field_name: value}, f"{field_name} is empty on the record", [_evidence(fact)])
        )
    elif str(current).upper() != str(value).upper():
        proposals.append(
            Proposal(
                "conflict",
                entity,
                record["id"],
                {field_name: {"record": current, "message": value}},
                f"message says {value!r}, record has {current!r}; not overwritten",
                [_evidence(fact)],
            )
        )


def build_plan(chat_key: str, messages: list[Message], snapshot: dict, display_name: str | None = None) -> Plan:
    chat_key = normalize_phone(chat_key) or chat_key
    state = snapshot.get("import_state", {}).get(chat_key, {})
    seen = set(state.get("fingerprints", []))
    watermark = state.get("watermark_utc")
    watermark_dt = datetime.fromisoformat(watermark) if watermark else None

    fresh: list[Message] = []
    fresh_fps: list[str] = []
    skipped = 0
    for message in messages:
        fp = fingerprint(chat_key, message)
        if fp in seen or fp in fresh_fps:
            skipped += 1
            continue
        fresh.append(message)
        fresh_fps.append(fp)

    proposals: list[Proposal] = []
    business = set(snapshot.get("business_senders", []))
    facts = []
    for m in fresh:
        if not m.sender:
            continue  # system lines ("Messages are end-to-end encrypted")
        for f in extract_facts(m):
            if f.kind == "payment_reported" and m.sender in business:
                f = Fact("amount", f.value, f.message, f.snippet)  # our own "paid" wording is not a client report
            facts.append(f)

    prospects = _records_for(snapshot, "prospects", chat_key)
    services = _records_for(snapshot, "services", chat_key)
    bookings = _records_for(snapshot, "bookings", chat_key)

    if fresh and not (prospects or services or bookings):
        first = next((m for m in fresh if m.sender), fresh[0])
        dates = sorted({f.value for f in facts if f.kind == "date_mentioned"})
        travel = dates[0].isoformat() if dates else None
        proposals.append(
            Proposal(
                "create",
                "prospect",
                None,
                {
                    "Client": display_name or chat_key,
                    "Phone": chat_key,
                    "Travel date": travel,
                    "Source key": f"{chat_key}|{travel or message_local_date(first).isoformat()}",
                    "Stage": "Details needed",
                    "Waiting on": "Us",
                },
                "no record has this chat key (phone); names are not used for matching",
                [f"{iso_rome(first.timestamp_utc)} first new message"],
            )
        )

    booking = bookings[0] if len(bookings) == 1 else None
    if len(bookings) > 1 and any(f.kind in ("payment_reported", "amount") for f in facts):
        proposals.append(
            Proposal(
                "conflict",
                "booking",
                None,
                {"candidates": [b["id"] for b in bookings]},
                "several bookings share this chat key; money facts need a person to pick the booking",
                [_evidence(f) for f in facts if f.kind in ("payment_reported", "amount")][:5],
            )
        )

    existing_payments = [p for p in snapshot.get("payments", []) if booking and p.get("booking_id") == booking["id"]]
    for fact in facts:
        if fact.kind == "payment_reported":
            sent = message_local_date(fact.message)
            duplicate = next(
                (
                    p
                    for p in existing_payments
                    if abs(float(p.get("amount_eur") or 0) - float(fact.value)) < 0.005
                    and abs((datetime.fromisoformat(p["date"]).date() - sent).days) <= PAYMENT_DEDUP_WINDOW_DAYS
                ),
                None,
            )
            if duplicate:
                proposals.append(
                    Proposal("skip", "payment", duplicate["id"], {"Amount EUR": fact.value}, "same amount already recorded within 3 days", [_evidence(fact)])
                )
                continue
            proposals.append(
                Proposal(
                    "create",
                    "payment",
                    None,
                    {
                        "Booking": booking["id"] if booking else None,
                        "Type": "Receipt",
                        "Amount EUR": fact.value,
                        "Currency": "EUR",
                        "Date": sent.isoformat(),
                        "Verification status": "Reported by client",
                    },
                    "client message reports a payment; it stays unverified until checked against the account"
                    + ("" if booking else "; no single booking matched, link it by hand"),
                    [_evidence(fact)],
                )
            )
        elif fact.kind == "amount":
            for b in bookings[:1] if booking else []:
                if b.get("package_eur") not in (None, "") and abs(float(b["package_eur"]) - float(fact.value)) >= 0.005:
                    # Only flag amounts large enough to be a total, not a deposit or a fee.
                    if float(fact.value) >= 0.5 * float(b["package_eur"]):
                        _compare(proposals, "booking", b, "package_eur", fact.value, fact)
        elif fact.kind in ("pax", "flight"):
            target = services[0] if len(services) == 1 else None
            if target:
                _compare(proposals, "service", target, fact.kind, fact.value, fact)
            elif services:
                proposals.append(
                    Proposal("conflict", "service", None, {fact.kind: fact.value, "candidates": [s["id"] for s in services]}, "several service legs share this chat key", [_evidence(fact)])
                )
        elif fact.kind == "date_mentioned" and services:
            known = {s.get("service_date") for s in services}
            if fact.value.isoformat() not in known:
                proposals.append(
                    Proposal(
                        "conflict",
                        "service",
                        None,
                        {"date_mentioned": fact.value.isoformat(), "record_dates": sorted(d for d in known if d)},
                        "date in the message does not match any service leg for this chat; check before re-dating",
                        [_evidence(fact)],
                    )
                )

    newest = max((m.timestamp_utc for m in fresh), default=None)
    if newest and watermark_dt and watermark_dt > newest:
        newest = watermark_dt
    return Plan(
        chat_key=chat_key,
        new_messages=len(fresh),
        skipped_already_seen=skipped,
        proposals=proposals,
        new_watermark_utc=newest.astimezone(timezone.utc).isoformat() if newest else watermark,
        new_fingerprints=fresh_fps,
    )
