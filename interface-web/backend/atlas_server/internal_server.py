"""Point d'entrée interne d'Atlas sur 127.0.0.1 (jamais exposé au réseau).

* ``POST /internal/event`` : le serveur d'outils lancé par Hermes (``mcp-stdio``) signale une
  modification (tâche ajoutée…) ; Atlas la diffuse aux fenêtres ouvertes.
* ``POST /mcp`` : les mêmes outils en MCP HTTP, pour qui préfère cette connexion.
Les deux exigent le jeton ``ATLAS_MCP_TOKEN``.
"""

from __future__ import annotations

import hmac
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .mcp_tools import handle_message

FORWARDED_TYPES = {"tasks.changed", "priorities.changed", "notes.changed", "finance.changed", "activity",
                   "pages.changed", "cockpit.changed", "calls.changed"}


def create_internal_app(main_app: FastAPI, token_getter: Callable[[], str]) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request: Request) -> bool:
        host = request.client.host if request.client else ""
        token = token_getter()
        header = request.headers.get("authorization", "")
        return host in ("127.0.0.1", "::1") and bool(token) and hmac.compare_digest(
            header.encode(), f"Bearer {token}".encode())

    def state() -> Any:
        return getattr(main_app.state, "atlas", None)

    @app.post("/internal/event")
    async def internal_event(request: Request):
        st = state()
        if not authorized(request):
            return JSONResponse({"error": "non autorisé"}, status_code=401)
        if st is None:
            return JSONResponse({"error": "Atlas démarre"}, status_code=503)
        event = await request.json()
        if not isinstance(event, dict) or event.get("type") not in FORWARDED_TYPES:
            return JSONResponse({"error": "événement refusé"}, status_code=400)
        st.mark_mcp_seen()
        await st.hub.broadcast(event)
        return {"ok": True}

    @app.post("/mcp")
    async def mcp_post(request: Request):
        st = state()
        if not authorized(request):
            return JSONResponse({"error": "non autorisé"}, status_code=401)
        if st is None:
            return JSONResponse({"error": "Atlas démarre"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}},
                                status_code=400)
        messages = body if isinstance(body, list) else [body]
        replies = [r for r in [await handle_message(st, m) for m in messages if isinstance(m, dict)] if r]
        if not replies:
            return Response(status_code=202)
        return JSONResponse(replies if isinstance(body, list) else replies[0])

    @app.get("/mcp")
    async def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    return app
