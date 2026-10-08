"""Rome Tours WhatsApp import worker.

Reads a WhatsApp chat export plus a local JSON snapshot of the Notion
records and produces a *proposal* (creates, updates, conflicts). It never
writes to Notion: the writer is intentionally "Not connected".
"""

__version__ = "0.1.0"
