# Rome Tours: WhatsApp import worker (dry-run)

This worker supports the Rome Tours Notion operations hub. It is separate from the Atlas application in this repository and shares no code with it.

**Status: background sync "Not connected".** The worker only produces a *proposal*. It has no Notion credentials and no writer. `apply` refuses on purpose (exit code 3) until a reviewed, server-side writer exists.

## What it does

1. **Parses** a WhatsApp `.txt` export.
   - Handles the iOS `[dd/mm/yyyy, hh:mm:ss]` layout and the Android `dd/mm/yy, hh:mm -` layout.
   - Handles 12-hour and 24-hour times and multi-line messages.
   - Flags attachments ("image omitted").
   - Converts each timestamp from the exporting phone's timezone (default `Europe/Rome`, DST-aware) to UTC.
2. **Deduplicates.**
   - Every message gets a fingerprint: `sha256(chat key | UTC time | sender | normalised text)`.
   - A repeated or overlapping export re-proposes nothing that the previous batch already recorded.
3. **Matches** on the chat key, which is the phone number in E.164 form taken from the export file name.
   - It never matches on a client name alone.
   - It never guesses a missing country code.
4. **Extracts candidate facts**: EUR amounts, "paid/sent" payment reports, pax counts, flight numbers and dates mentioned.
   - It uses rules only. No AI call is made.
   - Message text is data: an instruction written in a chat is never followed.
5. **Plans** the changes:
   - `create`: a new prospect for an unknown chat, or a payment.
     - A payment is always `Verification status = Reported by client`. A client message never makes a payment verified.
   - `update`: only fills fields that are empty on the record.
   - `conflict`: the message disagrees with a non-empty field. Examples: a different pax count, a different total, or a date that matches no service leg. The record is never overwritten. A person decides, and conflicts should become Actions in Notion.
   - `skip`: the same payment amount was already recorded within 3 days.

## Usage

```bash
cd integrations/rome_tours_import
python3 -m rt_import plan \
  --export "WhatsApp Chat with +39 3xx xxx xxxx.txt" \
  --snapshot snapshot.json \
  --out proposal.json
```

Options:
- `--tz`: the exporting phone's timezone.
- `--month-first`: for M/D/Y exports.
- `--chat-key`: when the file name has no number.
- `--name`: display name for a new prospect.

### Snapshot format

The snapshot is local JSON exported from Notion (see `tests/fixtures/snapshot.json`):

```json
{
  "business_senders": ["Rome Tours"],
  "services": [{"id": "...", "phone": "+...", "service_date": "YYYY-MM-DD", "pax": 4, "flight": null}],
  "prospects": [{"id": "...", "phone": "+..."}],
  "bookings": [{"id": "...", "phone": "+...", "package_eur": 220}],
  "payments": [{"id": "...", "booking_id": "...", "amount_eur": 22, "date": "YYYY-MM-DD"}],
  "import_state": {"+...": {"watermark_utc": "...", "fingerprints": ["..."]}}
}
```

`batch_update` in the output gives the new watermark and the number of fingerprints to store with the Update Batch, so the next import skips them.

## Tests

The tests use the standard library only. All fixtures are synthetic: reserved `+1 555 01xx` numbers and fake names.

```bash
python3 -m unittest discover -s tests -v
```

The tests cover:
- both export layouts
- the DST change
- phone normalisation
- extraction
- reported payments never becoming verified
- injection-like text staying inert
- empty-field updates versus conflicts
- idempotent repeated uploads and overlapping uploads
- duplicate payments
- matching that ignores names
- ambiguous bookings
- dates that match no service leg
- dry-run writing nothing
- `apply` refusing

## Not done yet

- **No Notion writer.** If one is built, it must run server-side with its own credential, never in a browser or a chat. It should create the Update Batch first, then apply only the proposals a person has approved.
- **No AI extraction.** Free-text understanding (for example "the day after we land") is left to the person reviewing.
- **Rule limits.** Extraction misses amounts written in words, and it does not convert non-EUR currencies.
