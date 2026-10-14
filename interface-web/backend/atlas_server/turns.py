"""Orchestration des tours de conversation et diffusion des événements temps réel.

Règles :
* une seule exécution Hermes active à la fois (Atlas est « occupé » sinon) ;
* chaque envoi porte un ``request_id`` généré par le navigateur : un même
  identifiant n'est jamais exécuté deux fois (double clic, reconnexion) ;
* aucune relance automatique d'un tour déjà commencé ;
* toutes les fenêtres ouvertes (Windows, Mac) reçoivent les mêmes mises à jour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .db import Database
from .hermes_adapter import HermesError
from .calls import call_context, is_goodnight
from .util import french_datetime, redact

log = logging.getLogger("atlas.turns")

DEFAULT_TITLE = "Nouvelle conversation"

TOOL_LABELS: list[tuple[str, str]] = [
    ("mcp_atlas_tache_ajouter", "J'ajoute une tâche à ton tableau"),
    ("mcp_atlas_tache_modifier", "Je modifie une tâche"),
    ("mcp_atlas_tache_terminer", "Je coche une tâche"),
    ("mcp_atlas_tache_supprimer", "Je supprime une tâche"),
    ("mcp_atlas_taches_lister", "Je regarde tes tâches"),
    ("mcp_atlas_journee", "Je regarde ta journée"),
    ("mcp_atlas_priorite", "Je mets à jour tes priorités"),
    ("mcp_atlas_note_ajouter", "J'ajoute une note"),
    ("mcp_atlas_notes_chercher", "Je cherche dans tes notes"),
    ("mcp_atlas_cockpit", "Je regarde ton cockpit 120K"),
    ("mcp_atlas_action_du_jour", "Je fixe l'action du jour qui rapporte"),
    ("mcp_atlas_coucher_noter", "Je note ton heure de coucher"),
    ("mcp_atlas_page_ecrire", "J'écris dans une page partagée"),
    ("mcp_atlas_page_supprimer", "Je supprime une page partagée"),
    ("mcp_atlas_page", "Je consulte les pages partagées"),
    ("memory", "Je consulte ma mémoire"),
    ("session_search", "Je retrouve nos anciennes conversations"),
    ("skill_view", "J'ouvre une compétence"),
    ("skills_list", "Je regarde mes compétences"),
    ("skill_manage", "Je mets à jour une compétence"),
    ("web_search", "Je cherche sur le web"),
    ("web_extract", "Je lis une page web"),
    ("browser", "Je navigue sur le web"),
    ("read_file", "Je lis un fichier"),
    ("search_files", "Je cherche dans des fichiers"),
    ("write_file", "J'écris un fichier"),
    ("patch", "Je modifie un fichier"),
    ("terminal", "J'exécute une commande"),
    ("process", "Je surveille un processus"),
    ("execute_code", "J'exécute du code"),
    ("todo", "Je mets à jour ma liste de tâches"),
    ("delegate", "Je délègue une sous-tâche"),
    ("cronjob", "Je consulte les tâches planifiées"),
    ("send_message", "J'envoie un message"),
    ("text_to_speech", "Je prépare un audio"),
    ("vision", "J'analyse une image"),
    ("image_generate", "Je génère une image"),
    ("clarify", "J'ai une question pour toi"),
]


ATLAS_TOOL_RE = re.compile(r"atlas_{1,2}([a-z_]+)")


def tool_label(tool: str, preview: str | None = None) -> str:
    # Outils d'Atlas : « mcp_atlas_x », « mcp__atlas__x », ou appelés via le pont « tool_call » d'Hermes.
    match = ATLAS_TOOL_RE.search(f"{tool} {preview or ''}".lower()) if "atlas" in f"{tool}{preview}".lower() else None
    if match:
        tool = "mcp_atlas_" + match.group(1)
    lowered = (tool or "").lower()
    for key, label in TOOL_LABELS:
        if key in lowered:
            return label
    return f"J'utilise l'outil « {tool} »"


class Hub:
    """Connexions WebSocket des navigateurs ouverts."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, message: dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class Busy(Exception):
    pass


class Unavailable(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class ActiveTurn:
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    request_id: str
    origin_device: str
    want_tts: bool
    started_at: float = field(default_factory=time.time)
    run_id: str | None = None
    content: str = ""
    state: str = "thinking"
    label: str = "Je réfléchis…"
    approval: dict[str, Any] | None = None
    stop_requested: bool = False
    tools_started: dict[str, float] = field(default_factory=dict)
    context_extra: list[str] = field(default_factory=list)
    task: asyncio.Task | None = None
    last_flush: float = 0.0


class TurnManager:
    def __init__(self, db: Database, adapter: Any, hub: Hub, settings: Any):
        self.db = db
        self.adapter = adapter
        self.hub = hub
        self.settings = settings
        self.active: ActiveTurn | None = None
        self._lock = asyncio.Lock()
        self.mcp_connected = lambda: False
        self.calls: Any = None  # planificateur d'appels (heure de coucher)

    # -- utilitaires ---------------------------------------------------------
    def timezone(self) -> str:
        return self.db.get_preferences().get("timezone") or "Africa/Casablanca"

    async def activity(self, category: str, label: str, status: str = "info", **kw: Any) -> None:
        if kw.get("detail"):
            kw["detail"] = redact(str(kw["detail"]))[:500]
        event = self.db.add_event(category, label, status, **kw)
        await self.hub.broadcast({"type": "activity", "event": event})

    def state_payload(self) -> dict[str, Any]:
        turn = self.active
        if not turn:
            return {"type": "atlas.state", "state": "idle", "label": "Disponible"}
        return {"type": "atlas.state", "state": turn.state, "label": turn.label,
                "conversation_id": turn.conversation_id, "message_id": turn.assistant_message_id,
                "run_id": turn.run_id, "approval": turn.approval, "started_at": turn.started_at,
                "can_stop": bool(self.adapter.status.can_stop or self.adapter.status.mode == "demo")}

    async def _set_state(self, state: str, label: str) -> None:
        if self.active:
            self.active.state, self.active.label = state, label
        await self.hub.broadcast(self.state_payload())

    def snapshot(self) -> dict[str, Any]:
        snap = self.state_payload()
        if self.active:
            snap["partial_content"] = self.active.content
        return snap

    def build_context(self, kind: str, extra: list[str] | None = None) -> str:
        tz = self.timezone()
        parts = [
            "Contexte transmis automatiquement par l'interface Atlas (informations, pas une demande) :",
            f"date et heure actuelles : {french_datetime(tz)} (fuseau {tz}).",
        ]
        if kind == "voice":
            parts.append("Ce message a été dicté à la voix (appel en direct) et ta réponse sera lue à voix haute : "
                         "reste naturel et concis.")
        if self.mcp_connected():
            parts.append("Les outils mcp_atlas_* agissent sur son tableau de bord Atlas (tâches, priorités, notes), "
                         "visible à l'écran : utilise-les quand il te confie une tâche ou demande ce qu'il reste à faire.")
        return " ".join(parts + (extra or []))

    # -- envoi -------------------------------------------------------------------
    async def submit(self, conversation_id: str, request_id: str, content: str, *, device_id: str,
                     kind: str = "text", want_tts: bool = False, note_ids: list[str] | None = None,
                     call_slot: str | None = None, call_start: bool = False, call_end: bool = False) -> dict:
        existing = self.db.get_request(request_id)
        if existing:
            return {"duplicate": True, "user_message": self.db.get_message(existing["user_message_id"]),
                    "assistant_message": self.db.get_message(existing["assistant_message_id"])}
        conv = self.db.get_conversation(conversation_id)
        if not conv:
            raise KeyError("conversation")
        async with self._lock:
            if self.active:
                raise Busy()
            status = self.adapter.status
            if not status.authenticated:
                status = await self.adapter.probe()
                await self.hub.broadcast({"type": "hermes.status", "status": status.public()})
            if not status.authenticated:
                raise Unavailable(status.error_kind or "unavailable",
                                  status.error or "Hermes n'est pas disponible.")
            message_for_hermes = content
            attached = []
            for nid in note_ids or []:
                note = self.db.get_note(nid)
                if note:
                    attached.append(note)
            if attached:
                blocks = "\n\n".join(f"« {n['content'].strip()} »" for n in attached)
                message_for_hermes = f"{content}\n\n[Note jointe depuis mon tableau de bord]\n{blocks}"
            user_msg = self.db.add_message(
                conversation_id, "user", message_for_hermes, request_id=request_id, origin_device=device_id,
                meta={"kind": kind, "notes": [n["id"] for n in attached]})
            assistant_msg = self.db.add_message(
                conversation_id, "assistant", "", status="streaming", request_id=request_id,
                origin_device=device_id, meta={"want_tts": want_tts})
            self.db.add_request(request_id, conversation_id, user_msg["id"], assistant_msg["id"])
            if conv["title"] == DEFAULT_TITLE:
                title = " ".join(content.split())[:60] or DEFAULT_TITLE
                self.db.update_conversation(conversation_id, title=title)
            turn = ActiveTurn(conversation_id, user_msg["id"], assistant_msg["id"], request_id, device_id, want_tts)
            turn.context_extra = call_context(call_slot, call_start, call_end)
            if self.calls is not None and is_goodnight(content):
                info = self.calls.note_bedtime()
                turn.context_extra.append(f"Il te dit bonne nuit : Atlas a noté son coucher à {info['coucher']} ; "
                                          f"appel du matin prévu à {info['appel_du_matin']}. Dis-le-lui en une phrase.")
            self.active = turn
        await self.hub.broadcast({"type": "message.created", "message": user_msg})
        await self.hub.broadcast({"type": "message.created", "message": assistant_msg})
        await self.hub.broadcast({"type": "conversations.changed"})
        await self.hub.broadcast(self.state_payload())
        await self.activity("conversation", "Demande reçue" + (" (voix)" if kind == "voice" else ""), "ok",
                            conversation_id=conversation_id)
        turn.task = asyncio.create_task(self._run(turn, message_for_hermes, kind))
        return {"duplicate": False, "user_message": user_msg, "assistant_message": assistant_msg}

    # -- exécution ------------------------------------------------------------------
    async def _ensure_session(self, conversation_id: str, force_new: bool = False) -> str:
        conv = self.db.get_conversation(conversation_id)
        if conv and conv["hermes_session_id"] and not force_new:
            return conv["hermes_session_id"]
        session_id = await self.adapter.create_session(conv["title"] if conv else None)
        self.db.update_conversation(conversation_id, hermes_session_id=session_id)
        await self.activity("hermes", "Nouvelle session Hermes ouverte", "ok", detail=session_id,
                            conversation_id=conversation_id)
        return session_id

    async def _on_event(self, turn: ActiveTurn, event: dict[str, Any]) -> None:
        etype = event["type"]
        if etype == "run_started":
            turn.run_id = event.get("run_id")
            self.db.update_message(turn.assistant_message_id, run_id=turn.run_id)
            await self._set_state("thinking", "Je réfléchis…")
        elif etype == "delta":
            turn.content += event["text"]
            await self.hub.broadcast({"type": "message.delta", "message_id": turn.assistant_message_id,
                                      "conversation_id": turn.conversation_id, "delta": event["text"]})
            if turn.state != "thinking" or turn.label != "Je réponds…":
                await self._set_state("thinking", "Je réponds…")
            if time.monotonic() - turn.last_flush > 1.5:
                turn.last_flush = time.monotonic()
                self.db.update_message(turn.assistant_message_id, content=turn.content)
        elif etype == "tool_started":
            label = tool_label(event["tool"], event.get("preview"))
            turn.tools_started[event["tool"]] = time.monotonic()
            await self._set_state("acting", label)
            await self.activity("outil", f"{label}", "running", detail=f"{event['tool']} · {event.get('preview')}",
                                run_id=turn.run_id, conversation_id=turn.conversation_id)
        elif etype == "tool_completed":
            started = turn.tools_started.pop(event["tool"], None)
            duration = int((time.monotonic() - started) * 1000) if started else None
            await self.activity("outil", f"{tool_label(event['tool'], event.get('preview'))} — " + ("échec" if event["failed"] else "terminé"),
                                "error" if event["failed"] else "ok", duration_ms=duration,
                                detail=f"{event['tool']} · {event.get('preview')}", run_id=turn.run_id,
                                conversation_id=turn.conversation_id)
            await self._set_state("thinking", "Je réfléchis…")
        elif etype == "commentary":
            await self.activity("hermes", "Point d'étape d'Atlas", "info", detail=event["text"], run_id=turn.run_id,
                                conversation_id=turn.conversation_id)
        elif etype == "approval_request":
            turn.approval = {k: event.get(k) for k in ("request_id", "command", "description", "choices")}
            await self._set_state("waiting_approval", "J'attends ton accord")
            await self.activity("approbation", "Approbation demandée par Hermes", "waiting",
                                detail=event.get("description") or event.get("command"), run_id=turn.run_id,
                                conversation_id=turn.conversation_id)
        elif etype == "approval_resolved":
            turn.approval = None
            await self._set_state("thinking", "Je reprends…")

    async def _run(self, turn: ActiveTurn, message: str, kind: str) -> None:
        final: dict[str, Any] | None = None
        error: HermesError | None = None
        started = time.monotonic()
        try:
            try:
                session_id = await self._ensure_session(turn.conversation_id)
                final = await self.adapter.stream_turn(
                    session_id, message, system_message=self.build_context(kind, turn.context_extra),
                    on_event=lambda e: self._on_event(turn, e))
            except HermesError as exc:
                if exc.kind != "session_not_found":
                    raise
                # 404 avant tout démarrage : relancer sur une nouvelle session est sans risque.
                await self.activity("hermes", "Session Hermes introuvable : nouvelle session", "warning",
                                    conversation_id=turn.conversation_id)
                session_id = await self._ensure_session(turn.conversation_id, force_new=True)
                final = await self.adapter.stream_turn(
                    session_id, message, system_message=self.build_context(kind, turn.context_extra),
                    on_event=lambda e: self._on_event(turn, e))
        except HermesError as exc:
            error = exc
        except Exception as exc:  # erreur imprévue : on la montre, sans relancer
            log.exception("Tour en échec")
            error = HermesError("internal", f"Erreur interne d'Atlas : {type(exc).__name__}")
        if error and error.kind == "stream_lost" and turn.run_id:
            run = await self.adapter.get_run(turn.run_id)
            if run and run.get("status") == "completed":
                final = {"type": "completed", "status": "completed", "content": run.get("output") or turn.content,
                         "session_id": run.get("session_id"), "usage": run.get("usage") or {},
                         "runtime": run.get("runtime") or {}, "run_id": turn.run_id}
                error = None
        await self._finish(turn, final, error, int((time.monotonic() - started) * 1000))

    async def _finish(self, turn: ActiveTurn, final: dict | None, error: HermesError | None, duration_ms: int) -> None:
        meta = {"want_tts": turn.want_tts}
        if error:
            status = "interrupted" if error.kind == "stream_lost" else "error"
            content = turn.content
            meta.update({"error_kind": error.kind, "error": str(error)})
            await self.activity("hermes", "Tâche interrompue" if status == "interrupted" else "Erreur Hermes",
                                "error", duration_ms=duration_ms, detail=str(error), run_id=turn.run_id,
                                conversation_id=turn.conversation_id)
        else:
            assert final is not None
            run_status = final.get("status")
            content = final.get("content") or turn.content
            if final.get("session_id"):
                conv = self.db.get_conversation(turn.conversation_id)
                if conv and conv["hermes_session_id"] != final["session_id"]:
                    # Hermes peut faire tourner l'identifiant après une compression de contexte.
                    self.db.update_conversation(turn.conversation_id, hermes_session_id=final["session_id"])
            meta.update({"usage": final.get("usage") or {}, "runtime": final.get("runtime") or {}})
            if run_status == "completed":
                status = "complete"
                await self.activity("conversation", "Réponse disponible", "ok", duration_ms=duration_ms,
                                    run_id=turn.run_id, conversation_id=turn.conversation_id)
                runtime = final.get("runtime") or {}
                if runtime.get("model"):
                    self.db.set_preference("last_runtime", {
                        "provider": runtime.get("provider"), "model": runtime.get("model"), "at": time.time()})
            elif run_status == "cancelled":
                status = "cancelled"
                await self.activity("conversation", "Tâche interrompue à ta demande", "warning",
                                    duration_ms=duration_ms, run_id=turn.run_id,
                                    conversation_id=turn.conversation_id)
            else:
                status = "error"
                kind = final.get("failure_kind") or "provider"
                meta.update({"error_kind": kind, "error": final.get("error") or final.get("turn_exit_reason") or ""})
                await self.activity("hermes", "Hermes n'a pas pu terminer", "error", duration_ms=duration_ms,
                                    detail=meta["error"] or content, run_id=turn.run_id,
                                    conversation_id=turn.conversation_id)
        self.db.update_message(turn.assistant_message_id, content=content, status=status, meta=meta,
                               run_id=turn.run_id)
        self.active = None
        await self.hub.broadcast({"type": "message.completed", "message": self.db.get_message(turn.assistant_message_id)})
        await self.hub.broadcast(self.state_payload())

    # -- contrôle ------------------------------------------------------------------
    async def stop(self) -> dict:
        turn = self.active
        if not turn:
            return {"ok": False, "reason": "idle"}
        if not turn.run_id:
            return {"ok": False, "reason": "not_started"}
        turn.stop_requested = True
        ok = await self.adapter.stop_run(turn.run_id)
        await self.activity("conversation", "Arrêt demandé" if ok else "Arrêt impossible avec cette version d'Hermes",
                            "warning" if ok else "error", run_id=turn.run_id, conversation_id=turn.conversation_id)
        if ok:
            await self._set_state("thinking", "J'arrête…")
        return {"ok": ok}

    async def approve(self, choice: str) -> dict:
        turn = self.active
        if not turn or not turn.approval or not turn.run_id:
            return {"ok": False, "reason": "no_pending_approval"}
        result = await self.adapter.answer_approval(turn.run_id, choice, turn.approval.get("request_id"))
        await self.activity("approbation", "Approuvé (une fois)" if choice == "once" else
                            "Refusé" if choice == "deny" else f"Approuvé ({choice})",
                            "ok" if choice != "deny" else "warning", run_id=turn.run_id,
                            conversation_id=turn.conversation_id)
        turn.approval = None
        await self._set_state("thinking", "Je reprends…")
        return {"ok": True, "result": result}
