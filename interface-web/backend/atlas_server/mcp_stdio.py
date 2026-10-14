"""Serveur d'outils MCP d'Atlas en mode « stdio », lancé et géré par Hermes lui-même.

Hermes le démarre avec sa passerelle (``mcp_servers.atlas.command``) : les outils sont donc
disponibles quel que soit l'ordre de démarrage d'Atlas et d'Hermes. Ce processus léger :
* lit et écrit directement la base d'Atlas (SQLite en mode WAL, accès concurrent sûr) ;
* prévient le serveur Atlas, s'il tourne, par ``POST http://127.0.0.1:<port>/internal/event``
  pour que l'interface se mette à jour en direct (sinon, rien n'est perdu : tout est en base).

Protocole : JSON-RPC 2.0, un message JSON par ligne sur l'entrée/sortie standard.
Rien d'autre n'est écrit sur la sortie standard (journaux sur la sortie d'erreur).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from typing import Any

from .config import load_settings
from .db import Database
from .mcp_tools import handle_message
from .tasks import TaskService
from .util import redact

log = logging.getLogger("atlas.mcp.stdio")


class RemoteHub:
    """Transmet les événements au serveur Atlas s'il est lancé (meilleur effort, 1 s max)."""

    def __init__(self, port: int, token: str):
        self.url = f"http://127.0.0.1:{port}/internal/event"
        self.token = token

    def _post(self, event: dict) -> None:
        req = urllib.request.Request(self.url, data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # jamais via un proxy
        try:
            opener.open(req, timeout=1.0).close()
        except Exception:
            pass  # Atlas fermé : la modification est en base, l'interface la verra à l'ouverture

    async def broadcast(self, event: dict) -> None:
        await asyncio.to_thread(self._post, event)


class StdioState:
    def __init__(self) -> None:
        settings = load_settings()
        self.db = Database(settings.db_path)
        self.hub = RemoteHub(settings.mcp_port, settings.mcp_token)
        self.turns = self  # call_tool utilise st.turns.timezone() et st.turns.activity()
        self.tasks = TaskService(self.db, self.hub, self.activity, self.timezone)
        self._seen = 0.0
        # Qui utilise ces outils : « atlas » (Hermes, par défaut) ou « claude » (Claude Code)
        self.agent = os.environ.get("ATLAS_MCP_AGENT", "").strip().lower() or "atlas"

    def timezone(self) -> str:
        return self.db.get_preferences().get("timezone") or "Africa/Casablanca"

    async def activity(self, category: str, label: str, status: str = "info", **kw: Any) -> None:
        if kw.get("detail"):
            kw["detail"] = redact(str(kw["detail"]))[:500]
        event = self.db.add_event(category, label, status, **kw)
        await self.hub.broadcast({"type": "activity", "event": event})

    def mark_mcp_seen(self) -> None:
        now = time.time()
        if now - self._seen > 60:
            # le voyant « Hermes connecté » de l'interface ne doit suivre qu'Hermes
            self.db.set_preference("mcp_last_seen" if self.agent == "atlas" else f"mcp_last_seen_{self.agent}", now)
            self._seen = now


async def serve_stdio() -> None:
    st = StdioState()
    loop = asyncio.get_running_loop()
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    while True:
        line = await loop.run_in_executor(None, reader.readline)
        if not line:
            break  # Hermes a fermé la connexion
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            reply: Any = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}}
        else:
            if isinstance(message, list):
                replies = [r for r in [await handle_message(st, m) for m in message if isinstance(m, dict)] if r]
                reply = replies or None
            elif isinstance(message, dict):
                reply = await handle_message(st, message)
            else:
                reply = None
        if reply is not None:
            writer.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            writer.flush()
    st.db.close()


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    asyncio.run(serve_stdio())
    return 0
