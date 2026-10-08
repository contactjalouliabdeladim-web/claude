"""Tests use synthetic fixtures only (reserved +1 555 01xx numbers, fake names)."""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_import.cli import NOT_CONNECTED, main  # noqa: E402
from rt_import.extract import extract_facts  # noqa: E402
from rt_import.normalize import chat_key_from_filename, fingerprint, normalize_phone  # noqa: E402
from rt_import.parser import parse_export  # noqa: E402
from rt_import.planner import build_plan  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
IOS = FIX / "WhatsApp Chat with +1 555 010 0001.txt"
ANDROID = FIX / "android_new_lead.txt"
CHAT_A = "+15550100001"


def snapshot():
    data = json.loads((FIX / "snapshot.json").read_text(encoding="utf-8"))
    data["business_senders"] = ["Rome Tours"]
    return data


def actions(plan, action=None, entity=None):
    return [p for p in plan.proposals if (action is None or p.action == action) and (entity is None or p.entity == entity)]


class ParserTests(unittest.TestCase):
    def test_ios_lines_multiline_and_attachments(self):
        msgs = parse_export(IOS.read_text(encoding="utf-8"))
        self.assertEqual(len(msgs), 6)
        self.assertIn("luggage", msgs[2].text)  # continuation line joined
        self.assertTrue(msgs[4].has_attachment)
        # 01/10/2026 09:12:03 Rome (CEST, UTC+2) -> 07:12:03 UTC
        self.assertEqual(msgs[0].timestamp_utc, datetime(2026, 10, 1, 7, 12, 3, tzinfo=timezone.utc))

    def test_android_month_first_am_pm_and_system_line(self):
        msgs = parse_export(ANDROID.read_text(encoding="utf-8"), day_first=False)
        self.assertIsNone(msgs[0].sender)
        self.assertEqual(msgs[1].sender, "Test Guest B")
        self.assertEqual(msgs[2].timestamp_utc, datetime(2026, 10, 8, 17, 30, tzinfo=timezone.utc))

    def test_dst_change_uses_rome_offset(self):
        # Rome leaves summer time on 25 Oct 2026 at 03:00 local.
        before = parse_export("[24/10/2026, 12:00] X: a")[0].timestamp_utc
        after = parse_export("[26/10/2026, 12:00] X: a")[0].timestamp_utc
        self.assertEqual(before.hour, 10)  # UTC+2
        self.assertEqual(after.hour, 11)  # UTC+1


class NormalizeTests(unittest.TestCase):
    def test_phone_normalization_never_guesses_country(self):
        self.assertEqual(normalize_phone("+1 (555) 010-0001"), CHAT_A)
        self.assertEqual(normalize_phone("001 555 010 0001"), CHAT_A)
        self.assertIsNone(normalize_phone("555 010 0001"))
        self.assertEqual(chat_key_from_filename(IOS.name), CHAT_A)

    def test_fingerprint_ignores_whitespace_and_case(self):
        a = parse_export("[01/10/2026, 09:12] X: Hello  World")[0]
        b = parse_export("[01/10/2026, 09:12] X: hello world")[0]
        self.assertEqual(fingerprint(CHAT_A, a), fingerprint(CHAT_A, b))


class ExtractTests(unittest.TestCase):
    def test_amounts_pax_flight_dates(self):
        msgs = parse_export(IOS.read_text(encoding="utf-8"))
        kinds = {(f.kind, f.value) for m in msgs for f in extract_facts(m)}
        self.assertIn(("pax", 4), kinds)
        self.assertIn(("flight", "AZ611"), kinds)
        self.assertIn(("payment_reported", 22.0), kinds)
        self.assertIn(("amount", 220.0), kinds)
        self.assertTrue(any(k == "date_mentioned" and str(v) == "2026-10-13" for k, v in kinds))

    def test_thousands_separator(self):
        m = parse_export("[01/10/2026, 09:12] X: total is €1.770 for 3 vans")[0]
        self.assertEqual([f.value for f in extract_facts(m) if f.kind == "amount"], [1770.0])


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.msgs = parse_export(IOS.read_text(encoding="utf-8"))

    def test_reported_payment_is_never_verified(self):
        plan = build_plan(CHAT_A, self.msgs, snapshot())
        pays = actions(plan, "create", "payment")
        self.assertEqual(len(pays), 1)
        self.assertEqual(pays[0].fields["Verification status"], "Reported by client")
        self.assertEqual(pays[0].fields["Amount EUR"], 22.0)
        self.assertEqual(pays[0].fields["Booking"], "bk-A")

    def test_message_text_is_data_not_instructions(self):
        plan = build_plan(CHAT_A, self.msgs, snapshot())
        dumped = json.dumps(plan.as_dict(), default=str)
        self.assertNotIn("Fully paid", dumped)
        self.assertFalse(any(p.entity == "booking" and p.action == "update" for p in plan.proposals))

    def test_empty_field_update_and_no_overwrite(self):
        snap = snapshot()
        plan = build_plan(CHAT_A, self.msgs, snap)
        flight = [p for p in actions(plan, "update", "service") if "flight" in p.fields]
        self.assertEqual(flight[0].fields["flight"], "AZ611")
        snap["services"][0]["pax"] = 5  # record disagrees with the message
        plan = build_plan(CHAT_A, self.msgs, snap)
        conflict = [p for p in actions(plan, "conflict", "service") if "pax" in p.fields]
        self.assertEqual(conflict[0].fields["pax"], {"record": 5, "message": 4})
        self.assertFalse([p for p in actions(plan, "update", "service") if "pax" in p.fields])

    def test_repeated_upload_is_idempotent(self):
        snap = snapshot()
        first = build_plan(CHAT_A, self.msgs, snap)
        snap["import_state"] = {CHAT_A: {"watermark_utc": first.new_watermark_utc, "fingerprints": first.new_fingerprints}}
        again = build_plan(CHAT_A, self.msgs, snap)
        self.assertEqual(again.new_messages, 0)
        self.assertEqual(again.skipped_already_seen, len(self.msgs))
        self.assertEqual(again.proposals, [])

    def test_overlapping_upload_only_reviews_new_messages(self):
        snap = snapshot()
        first = build_plan(CHAT_A, self.msgs[:3], snap)
        snap["import_state"] = {CHAT_A: {"watermark_utc": first.new_watermark_utc, "fingerprints": first.new_fingerprints}}
        second = build_plan(CHAT_A, self.msgs, snap)
        self.assertEqual(second.new_messages, 3)
        self.assertEqual(second.skipped_already_seen, 3)

    def test_payment_already_recorded_is_skipped(self):
        snap = snapshot()
        snap["payments"] = [{"id": "pay-1", "booking_id": "bk-A", "amount_eur": 22, "date": "2026-10-06"}]
        plan = build_plan(CHAT_A, self.msgs, snap)
        self.assertEqual(actions(plan, "create", "payment"), [])
        self.assertEqual(len(actions(plan, "skip", "payment")), 1)

    def test_unknown_chat_creates_prospect_and_ignores_names(self):
        snap = snapshot()
        snap["prospects"].append({"id": "pro-same-name", "phone": "+15550100099", "client": "Test Guest B"})
        msgs = parse_export(ANDROID.read_text(encoding="utf-8"), day_first=False)
        plan = build_plan("+15550100002", msgs, snap, display_name="Test Guest B")
        created = actions(plan, "create", "prospect")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].fields["Travel date"], "2026-10-25")
        self.assertEqual(created[0].fields["Phone"], "+15550100002")
        self.assertFalse(any(p.target_id == "pro-same-name" for p in plan.proposals))

    def test_two_bookings_on_one_chat_needs_a_person(self):
        snap = snapshot()
        snap["bookings"].append({"id": "bk-A2", "phone": CHAT_A, "package_eur": 450})
        plan = build_plan(CHAT_A, self.msgs, snap)
        self.assertTrue(any(p.entity == "booking" and p.action == "conflict" for p in plan.proposals))
        self.assertIsNone(actions(plan, "create", "payment")[0].fields["Booking"])

    def test_date_not_matching_any_leg_is_flagged(self):
        msgs = self.msgs + parse_export("[08/10/2026, 08:00] Test Guest A: Actually can we move it to 14/10?")
        plan = build_plan(CHAT_A, msgs, snapshot())
        flagged = [p for p in actions(plan, "conflict", "service") if p.fields.get("date_mentioned") == "2026-10-14"]
        self.assertEqual(len(flagged), 1)


class CliTests(unittest.TestCase):
    def test_plan_is_a_dry_run_and_writes_nothing_else(self):
        before = sorted(p.name for p in FIX.iterdir())
        out = subprocess.run(
            [sys.executable, "-m", "rt_import", "plan", "--export", str(IOS), "--snapshot", str(FIX / "snapshot.json")],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        payload = json.loads(out.stdout)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["chat_key"], CHAT_A)
        self.assertEqual(before, sorted(p.name for p in FIX.iterdir()))

    def test_apply_refuses_while_not_connected(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["apply", "--export", str(IOS), "--snapshot", str(FIX / "snapshot.json")])
        self.assertEqual(code, NOT_CONNECTED)
        self.assertIn("Not connected", err.getvalue())


if __name__ == "__main__":
    unittest.main()
