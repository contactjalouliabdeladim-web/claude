"""Adaptateur Hermes — seul module qui parle à Hermes Agent.

Intégration retenue : le **serveur API officiel d'Hermes** (plateforme
``api_server`` du ``hermes gateway``), documenté dans
``website/docs/user-guide/features/api-server.md`` du dépôt Hermes.

Modes, choisis automatiquement d'après ``GET /v1/capabilities`` :

* ``sessions`` (préféré) — ``POST /api/sessions`` crée une session Hermes,
  ``POST /api/sessions/{id}/chat/stream`` exécute un tour avec un flux SSE
  d'événements réels (texte, outils, approbations, fin), ``POST
  /v1/runs/{run_id}/stop`` interrompt, ``POST /v1/runs/{run_id}/approval``
  répond à une demande d'approbation.
* ``chat_completions`` (repli pour une version plus ancienne) — ``POST
  /v1/chat/completions`` en streaming avec l'en-tête ``X-Hermes-Session-Id`` :
  l'historique est relu par Hermes depuis sa base. Pas d'arrêt ni d'approbation
  depuis l'interface dans ce mode.

Hermes garde la main sur : modèle et fournisseur, authentification du
fournisseur, personnalité (SOUL.md), mémoire, compétences, outils et règles
d'approbation. Atlas n'envoie que le message de l'utilisateur et un court
contexte éphémère (date, fuseau) via ``system_message``.

Important : si la connexion SSE vers Hermes est coupée, Hermes interrompt le
tour en cours (comportement documenté dans le code du serveur). Atlas ne
relance donc jamais automatiquement une requête : il consulte l'état du run
(``GET /v1/runs/{id}``) pour savoir ce qui s'est réellement passé.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

import httpx

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_QUOTA_RE = re.compile(
    r"(quota|rate.?limit|usage.?limit|too many requests|\b429\b|insufficient_quota|limite d'utilisation)",
    re.IGNORECASE)
_AUTH_PROVIDER_RE = re.compile(r"(unauthori[sz]ed|\b401\b|invalid api key|login required|re-?authenticate|token expired)",
                               re.IGNORECASE)


class HermesError(Exception):
    """Erreur d'intégration. ``kind`` sert à l'interface pour choisir le message."""

    def __init__(self, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass
class HermesStatus:
    reachable: bool = False
    authenticated: bool = False
    mode: str = "unavailable"  # sessions | chat_completions | unavailable
    error_kind: str | None = None
    error: str | None = None
    model_alias: str | None = None
    features: dict[str, Any] = field(default_factory=dict)
    checked_at: float = 0.0

    def public(self) -> dict[str, Any]:
        """Version sans données sensibles, envoyée au navigateur."""
        keep = ("session_chat_streaming", "run_stop", "run_approval_response", "approval_events",
                "tool_progress_events", "skills_api", "chat_completions_streaming")
        return {
            "reachable": self.reachable, "authenticated": self.authenticated, "mode": self.mode,
            "error_kind": self.error_kind, "error": self.error, "model_alias": self.model_alias,
            "features": {k: bool(self.features.get(k)) for k in keep}, "checked_at": self.checked_at,
        }

    @property
    def can_stop(self) -> bool:
        return self.mode == "sessions" and bool(self.features.get("run_stop"))

    @property
    def can_approve(self) -> bool:
        return self.mode == "sessions" and bool(
            self.features.get("run_approval_response") or self.features.get("approval_events"))


def classify_failure_text(text: str) -> str:
    if _QUOTA_RE.search(text or ""):
        return "quota"
    if _AUTH_PROVIDER_RE.search(text or ""):
        return "provider_auth"
    return "provider"


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, Any]]:
    """Décode un flux Server-Sent Events : renvoie (nom d'événement, données JSON)."""
    event_name = "message"
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                if raw.strip() == "[DONE]":
                    yield "done", {}
                else:
                    try:
                        yield event_name, json.loads(raw)
                    except json.JSONDecodeError:
                        yield event_name, {"raw": raw}
            event_name, data_lines = "message", []
            continue
        if line.startswith(":"):
            continue  # commentaire « keepalive »
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event_name = value.strip()
        elif name == "data":
            data_lines.append(value)


def _short(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


class HermesAdapter:
    def __init__(self, base_url: str, api_key: str, *, turn_timeout_s: int = 1800,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.turn_timeout_s = turn_timeout_s
        self.status = HermesStatus()
        self._transport = transport
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport,
            timeout=httpx.Timeout(15.0, read=60.0), headers=self._headers())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def close(self) -> None:
        await self._client.aclose()

    # -- état ------------------------------------------------------------------
    async def probe(self) -> HermesStatus:
        """Vérifie Hermes sans jamais appeler le modèle (``/health`` + ``/v1/capabilities``)."""
        status = HermesStatus(checked_at=time.time())
        if not self.api_key:
            status.error_kind, status.error = "no_key", "Aucune clé API Hermes n'est configurée dans atlas.env."
        try:
            health = await self._client.get("/health", timeout=5.0)
            status.reachable = health.status_code == 200
        except httpx.HTTPError:
            status.error_kind = "unreachable"
            status.error = f"Le serveur API d'Hermes ne répond pas sur {self.base_url}."
            self.status = status
            return status
        if not status.reachable:
            status.error_kind, status.error = "unreachable", f"Réponse inattendue de Hermes ({health.status_code})."
            self.status = status
            return status
        if not self.api_key:
            self.status = status
            return status
        try:
            caps = await self._client.get("/v1/capabilities", timeout=8.0)
        except httpx.HTTPError as exc:
            status.error_kind, status.error = "unreachable", f"Hermes ne répond pas : {type(exc).__name__}"
            self.status = status
            return status
        if caps.status_code == 401:
            status.error_kind = "auth"
            status.error = "Hermes refuse la clé : HERMES_API_KEY (Atlas) ≠ API_SERVER_KEY (Hermes)."
        elif caps.status_code == 404:
            # Version ancienne sans /v1/capabilities : on teste l'authentification via /v1/models.
            models = await self._client.get("/v1/models", timeout=8.0)
            if models.status_code == 401:
                status.error_kind, status.error = "auth", "Hermes refuse la clé API d'Atlas."
            elif models.status_code == 200:
                status.authenticated, status.mode = True, "chat_completions"
                data = models.json().get("data") or []
                status.model_alias = data[0].get("id") if data else None
        elif caps.status_code == 200:
            body = caps.json()
            status.authenticated = True
            status.features = body.get("features") or {}
            status.model_alias = body.get("model")
            if status.features.get("session_chat_streaming"):
                status.mode = "sessions"
            elif status.features.get("chat_completions", True):
                status.mode = "chat_completions"
        else:
            status.error_kind, status.error = "protocol", f"/v1/capabilities a répondu {caps.status_code}."
        self.status = status
        return status

    async def list_skills(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/v1/skills", timeout=10.0)
        if resp.status_code != 200:
            raise HermesError("protocol", f"/v1/skills a répondu {resp.status_code}", resp.status_code)
        data = resp.json()
        items = data.get("data", data.get("skills", [])) if isinstance(data, dict) else data
        return [s for s in items if isinstance(s, dict) and s.get("name")]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        try:
            resp = await self._client.get(f"/v1/runs/{run_id}", timeout=10.0)
        except httpx.HTTPError:
            return None
        return resp.json() if resp.status_code == 200 else None

    # -- sessions ------------------------------------------------------------
    async def create_session(self, title: str | None = None) -> str:
        if self.status.mode != "sessions":
            # Mode de repli : Hermes crée la session au premier message portant cet identifiant.
            return f"atlas_{int(time.time())}_{secrets.token_hex(4)}"
        body: dict[str, Any] = {}
        if title:
            # Les titres de session Hermes sont uniques : suffixe aléatoire court.
            body["title"] = f"Atlas · {title}"[:80] + f" · {secrets.token_hex(3)}"
        resp = await self._client.post("/api/sessions", json=body)
        if resp.status_code == 401:
            raise HermesError("auth", "Hermes refuse la clé API d'Atlas.", 401)
        if resp.status_code not in (200, 201):
            raise HermesError("protocol", f"Création de session refusée ({resp.status_code}) : {_short(resp.text)}",
                              resp.status_code)
        session = resp.json().get("session") or {}
        session_id = session.get("id")
        if not session_id:
            raise HermesError("protocol", "Réponse de création de session sans identifiant.")
        return session_id

    # -- tour de conversation -----------------------------------------------------
    async def stream_turn(self, session_id: str, message: str, *, system_message: str | None = None,
                          on_event: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        """Exécute un tour. Appelle ``on_event`` pour chaque événement normalisé.

        Retourne l'événement terminal ``{"type": "completed", "status": ...}``.
        Lève ``HermesError`` si le tour n'a pas pu être lancé ou si le flux est perdu.
        """
        if not SESSION_ID_RE.match(session_id):
            raise HermesError("protocol", "Identifiant de session invalide.")
        if self.status.mode == "sessions":
            return await self._stream_session_turn(session_id, message, system_message, on_event)
        return await self._stream_chat_completions(session_id, message, system_message, on_event)

    async def _raise_for_start(self, resp: httpx.Response) -> None:
        if resp.status_code == 200:
            return
        body = (await resp.aread()).decode("utf-8", "replace")
        detail = _short(body)
        if resp.status_code == 401:
            raise HermesError("auth", "Hermes refuse la clé API d'Atlas.", 401)
        if resp.status_code == 404:
            raise HermesError("session_not_found", f"Session Hermes introuvable : {detail}", 404)
        if resp.status_code == 429:
            raise HermesError("busy", "Hermes exécute déjà trop de tâches en parallèle.", 429)
        if resp.status_code == 503:
            raise HermesError("unavailable", f"Hermes indisponible : {detail}", 503)
        raise HermesError("protocol", f"Hermes a refusé la requête ({resp.status_code}) : {detail}", resp.status_code)

    async def _stream_session_turn(self, session_id: str, message: str, system_message: str | None,
                                   on_event: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"message": message}
        if system_message:
            body["system_message"] = system_message
        timeout = httpx.Timeout(15.0, read=75.0)  # keepalive Hermes toutes les 10 s
        deadline = time.monotonic() + self.turn_timeout_s
        run_id: str | None = None
        content_parts: list[str] = []
        terminal: dict[str, Any] | None = None
        try:
            async with self._client.stream("POST", f"/api/sessions/{session_id}/chat/stream",
                                           json=body, timeout=timeout) as resp:
                await self._raise_for_start(resp)
                async for name, data in iter_sse(resp):
                    if not isinstance(data, dict):
                        continue
                    run_id = data.get("run_id") or run_id
                    if name == "run.started":
                        await _maybe_await(on_event({"type": "run_started", "run_id": run_id}))
                    elif name == "assistant.delta":
                        delta = data.get("delta") or ""
                        content_parts.append(delta)
                        await _maybe_await(on_event({"type": "delta", "text": delta}))
                    elif name == "assistant.commentary":
                        await _maybe_await(on_event({
                            "type": "commentary", "text": _short(data.get("text"), 400),
                            "already_streamed": bool(data.get("already_streamed"))}))
                    elif name in ("tool.started", "tool.completed", "tool.failed"):
                        tool = data.get("tool_name") or "outil"
                        if str(tool).startswith("_"):
                            continue  # événements internes (ex. réflexion) : jamais affichés
                        await _maybe_await(on_event({
                            "type": "tool_started" if name == "tool.started" else "tool_completed",
                            "tool": tool, "failed": name == "tool.failed",
                            "preview": _short(data.get("preview"))}))
                    elif name == "approval.request":
                        await _maybe_await(on_event({
                            "type": "approval_request", "run_id": run_id,
                            "request_id": data.get("request_id"),
                            "command": _short(data.get("command"), 600),
                            "description": _short(data.get("description"), 400),
                            "choices": data.get("choices") or ["once", "deny"]}))
                    elif name == "approval.responded":
                        await _maybe_await(on_event({"type": "approval_resolved", "choice": data.get("choice")}))
                    elif name == "assistant.completed":
                        terminal = {
                            "type": "completed", "content": data.get("content") or "".join(content_parts),
                            "session_id": data.get("session_id") or session_id,
                            "partial": bool(data.get("partial")), "interrupted": bool(data.get("interrupted")),
                            "turn_exit_reason": data.get("turn_exit_reason")}
                    elif name in ("run.completed", "run.failed", "run.cancelled"):
                        terminal = terminal or {"type": "completed", "content": "".join(content_parts),
                                                "session_id": data.get("session_id") or session_id}
                        terminal["status"] = name.split(".", 1)[1]
                        terminal["usage"] = data.get("usage") or {}
                        terminal["runtime"] = data.get("runtime") or {}
                        terminal["turn_exit_reason"] = data.get("turn_exit_reason") or terminal.get("turn_exit_reason")
                        if data.get("session_id"):
                            terminal["session_id"] = data["session_id"]
                    elif name == "run.queued":
                        terminal = {"type": "completed", "status": "failed", "session_id": session_id,
                                    "content": "", "turn_exit_reason": "queued_elsewhere"}
                    elif name == "error":
                        msg = _short(data.get("message"), 600)
                        terminal = {"type": "completed", "status": "failed", "session_id": session_id,
                                    "content": "".join(content_parts), "error": msg}
                    elif name == "done":
                        break
                    if time.monotonic() > deadline and run_id:
                        await self.stop_run(run_id)
                        deadline = float("inf")
        except httpx.HTTPError as exc:
            raise HermesError("stream_lost", f"Connexion à Hermes perdue ({type(exc).__name__}).") from exc
        if terminal is None or "status" not in terminal:
            raise HermesError("stream_lost", "Le flux d'Hermes s'est terminé sans résultat.")
        terminal["run_id"] = run_id
        if terminal["status"] == "failed":
            reason = " ".join(filter(None, [terminal.get("error"), terminal.get("content"),
                                            terminal.get("turn_exit_reason")]))
            terminal["failure_kind"] = classify_failure_text(reason)
        return terminal

    async def _stream_chat_completions(self, session_id: str, message: str, system_message: str | None,
                                       on_event: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": message})
        body = {"model": self.status.model_alias or "hermes-agent", "messages": messages, "stream": True}
        content_parts: list[str] = []
        usage: dict[str, Any] = {}
        returned_session = session_id
        finish_reason = None
        await _maybe_await(on_event({"type": "run_started", "run_id": None}))
        try:
            async with self._client.stream(
                    "POST", "/v1/chat/completions", json=body,
                    headers={"X-Hermes-Session-Id": session_id},
                    timeout=httpx.Timeout(15.0, read=float(self.turn_timeout_s))) as resp:
                await self._raise_for_start(resp)
                returned_session = resp.headers.get("X-Hermes-Session-Id", session_id)
                async for name, data in iter_sse(resp):
                    if name == "done":
                        break
                    if name == "hermes.tool.progress" and isinstance(data, dict):
                        tool = data.get("tool") or data.get("tool_name") or "outil"
                        if not str(tool).startswith("_"):
                            await _maybe_await(on_event({"type": "tool_started", "tool": tool,
                                                         "preview": _short(data.get("label") or data.get("preview"))}))
                        continue
                    if not isinstance(data, dict):
                        continue
                    if data.get("usage"):
                        usage = data["usage"]
                    for choice in data.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            content_parts.append(delta)
                            await _maybe_await(on_event({"type": "delta", "text": delta}))
                        finish_reason = choice.get("finish_reason") or finish_reason
        except httpx.HTTPError as exc:
            raise HermesError("stream_lost", f"Connexion à Hermes perdue ({type(exc).__name__}).") from exc
        content = "".join(content_parts)
        status = "completed" if content else "failed"
        result = {"type": "completed", "status": status, "content": content, "session_id": returned_session,
                  "usage": usage, "runtime": {}, "run_id": None, "finish_reason": finish_reason}
        if status == "failed":
            result["failure_kind"] = "provider"
        return result

    # -- contrôle ------------------------------------------------------------------
    async def stop_run(self, run_id: str) -> bool:
        if not run_id or not self.status.can_stop:
            return False
        try:
            resp = await self._client.post(f"/v1/runs/{run_id}/stop", json={}, timeout=10.0)
        except httpx.HTTPError:
            return False
        return resp.status_code in (200, 202)

    async def answer_approval(self, run_id: str, choice: str, request_id: str | None = None) -> dict[str, Any]:
        if choice not in ("once", "session", "always", "deny"):
            raise HermesError("invalid", "Choix d'approbation invalide.")
        body: dict[str, Any] = {"choice": choice}
        if request_id:
            body["request_id"] = request_id
        resp = await self._client.post(f"/v1/runs/{run_id}/approval", json=body, timeout=10.0)
        if resp.status_code != 200:
            raise HermesError("approval", f"Hermes n'a pas accepté la réponse ({resp.status_code}) : "
                                          f"{_short(resp.text)}", resp.status_code)
        return resp.json()


async def _maybe_await(value: Any) -> None:
    if hasattr(value, "__await__"):
        await value
