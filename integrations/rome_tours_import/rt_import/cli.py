"""Command line entry point.

    python -m rt_import plan  --export "WhatsApp Chat with +39 ....txt" --snapshot snapshot.json
    python -m rt_import apply ...   -> refuses: the Notion writer is Not connected.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .normalize import chat_key_from_filename
from .parser import parse_export
from .planner import build_plan

NOT_CONNECTED = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rt_import", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--export", required=True, type=Path, help="WhatsApp .txt export")
        p.add_argument("--snapshot", required=True, type=Path, help="JSON snapshot of the Notion records")
        p.add_argument("--chat-key", help="phone in +E.164 form; defaults to the number in the file name")
        p.add_argument("--tz", default="Europe/Rome", help="timezone of the exporting phone")
        p.add_argument("--month-first", action="store_true", help="export uses M/D/Y dates")
        p.add_argument("--name", help="display name for a new prospect")
        p.add_argument("--out", type=Path, help="write the proposal JSON here instead of stdout")
    args = parser.parse_args(argv)

    if args.command == "apply":
        print(
            "Notion writer: Not connected. Review the dry-run proposal and apply it by hand "
            "(or through a reviewed Claude session). Nothing was written.",
            file=sys.stderr,
        )
        return NOT_CONNECTED

    chat_key = args.chat_key or chat_key_from_filename(args.export.name)
    if not chat_key:
        print("Cannot tell the chat's phone number from the file name; pass --chat-key.", file=sys.stderr)
        return 2
    messages = parse_export(args.export.read_text(encoding="utf-8"), tz_name=args.tz, day_first=not args.month_first)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    plan = build_plan(chat_key, messages, snapshot, display_name=args.name)
    payload = json.dumps({"dry_run": True, **plan.as_dict()}, indent=2, ensure_ascii=False, default=str)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
