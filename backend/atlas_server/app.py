"""Application FastAPI : API du tableau de bord, WebSocket temps réel et interface compilée."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import __version__
from . import auth
from .certs import local_ip_addresses
from .config import Settings, load_settings
from .db import Database, new_id
from .demo import DemoAdapter
from .hermes_adapter import HermesAdapter, HermesError
from .tasks import TaskError, TaskService
from .turns import DEFAULT_TITLE, Busy, Hub, TurnManager, Unavailable
from .util import redact, today_iso, valid_timezone
from .voice.hermes_config import hermes_tts_settings
from .voice.stt import STTError, STTService
from .voice.tts import DEFAULT_VOICE, TTSError, TTSService

log = logging.getLogger("atlas")
STATIC_DIR = Path(__file__).parent / "static"
MAX_JSON_BYTES = 256 * 1024
DEFAULT_PREFS = {"timezone": "Africa/Casablanca", "autoplay_voice": True, "reduce_motion": "system",
                 "voice_confirm_before_send": False}
PUBLIC_PATHS = {"/api/public/status", "/api/auth/login", "/atlas-ca.crt"}
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


# -- modèles d'entrée -------------------------------------------------------------
class LoginBody(BaseModel):
    code: str = Field(min_length=4, max_length=64)
    device_label: str = Field(default="Appareil", max_length=80)


class MessageBody(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    content: str = Field(min_length=1, max_length=20000)
    device_id: str = Field(min_length=6, max_length=64)
    kind: str = Field(default="text", pattern="^(text|voice)$")
    want_tts: bool = False
    note_ids: list[str] = Field(default_factory=list, max_length=5)


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class PriorityBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    status: str = Field(default="todo", pattern="^(todo|doing|done)$")
    day: str | None = Field(default=None, max_length=10)
    due: str | None = Field(default=None, max_length=32)


class PriorityPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = Field(default=None, pattern="^(todo|doing|done)$")
    due: str | None = Field(default=None, max_length=32)
    clear_due: bool = False


class ReorderBody(BaseModel):
    day: str = Field(max_length=10)
    ids: list[str] = Field(max_length=20)


class NoteBody(BaseModel):
    content: str = Field(default="", max_length=20000)


class NotePatch(BaseModel):
    content: str = Field(max_length=20000)
    expected_updated_at: float | None = None


class ApprovalBody(BaseModel):
    choice: str = Field(pattern="^(once|session|always|deny)$")


class SpeakBody(BaseModel):
    message_id: str = Field(max_length=64)
    text: str | None = Field(default=None, max_length=4000)


class TaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)
    due_date: str | None = Field(default=None, max_length=20)
    due_time: str | None = Field(default=None, max_length=8)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    due_date: str | None = Field(default=None, max_length=20)
    due_time: str | None = Field(default=None, max_length=8)
    status: str | None = Field(default=None, pattern="^(todo|done)$")
    clear_date: bool = False
    clear_time: bool = False


class ClientActivity(BaseModel):
    event: str = Field(pattern="^(playback_started|playback_stopped|playback_finished|playback_blocked|"
                               "mic_denied|mic_unavailable)$")


CLIENT_ACTIVITY_LABELS = {
    "playback_started": ("voix", "Lecture audio démarrée", "ok"),
    "playback_stopped": ("voix", "Lecture audio interrompue", "warning"),
    "playback_finished": ("voix", "Lecture audio terminée", "ok"),
    "playback_blocked": ("voix", "Lecture automatique bloquée par le navigateur", "warning"),
    "mic_denied": ("voix", "Accès au micro refusé", "error"),
    "mic_unavailable": ("voix", "Micro indisponible sur cet appareil", "error"),
}


def scan_skill_dirs(root: Path, max_files: int = 500) -> list[dict]:
    """Noms et descriptions des compétences (``SKILL.md``), en lecture seule."""
    found: list[dict] = []
    if not root.is_dir():
        return found
    for path in list(root.glob("*/SKILL.md")) + list(root.glob("*/*/SKILL.md")):
        if len(found) >= max_files:
            break
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            continue
        name, desc = path.parent.name, ""
        if head.startswith("---"):
            for line in head.split("---", 2)[1].splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "name" and value.strip():
                    name = value.strip().strip("'\"")
                elif key.strip() == "description":
                    desc = value.strip().strip("'\"")
        found.append({"name": name, "description": desc})
    return found


def _valid_day(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(400, "Date invalide (AAAA-MM-JJ attendu).") from exc


class AtlasState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.hub = Hub()
        if settings.demo:
            self.adapter: Any = DemoAdapter()
        else:
            self.adapter = HermesAdapter(settings.hermes_url, settings.hermes_api_key,
                                         turn_timeout_s=settings.hermes_turn_timeout_s)
        self.turns = TurnManager(self.db, self.adapter, self.hub, settings)
        self.tasks = TaskService(self.db, self.hub, self.turns.activity, self.turns.timezone)
        self._mcp_seen = float(self.db.get_preferences().get("mcp_last_seen") or 0)
        self.turns.mcp_connected = lambda: bool(self.db.get_preferences().get("mcp_last_seen"))
        self.limiter = auth.LoginRateLimiter()
        hermes_tts = hermes_tts_settings(settings.hermes_home)
        voice = settings.tts_voice or hermes_tts.get("edge_voice") or DEFAULT_VOICE
        self.tts = TTSService(settings.tts_provider, voice, settings.hermes_dashboard_url,
                              settings.hermes_dashboard_token)
        self.stt = STTService(settings.stt_provider, settings.whisper_model, settings.hermes_dashboard_url,
                              settings.hermes_dashboard_token, models_dir=str(settings.data_dir / "modeles-voix"))
        self.hermes_tts = hermes_tts
        self.skills_cache: tuple[float, list[dict]] | None = None
        self.background: asyncio.Task | None = None

    def mark_mcp_seen(self) -> None:
        # Mémorisé en base (au plus une fois par minute) : survit à un redémarrage d'Atlas.
        now = time.time()
        if now - self._mcp_seen > 60:
            self.db.set_preference("mcp_last_seen", now)
            self._mcp_seen = now

    def preferences(self) -> dict[str, Any]:
        prefs = dict(DEFAULT_PREFS)
        prefs.update({k: v for k, v in self.db.get_preferences().items() if k in DEFAULT_PREFS})
        return prefs


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AtlasState(settings)
        app.state.atlas = state
        interrupted = state.db.mark_interrupted_messages()
        if interrupted:
            state.db.add_event("systeme", f"{interrupted} réponse(s) interrompue(s) par un arrêt du serveur",
                               "warning")
        auth.ensure_access_code(state.db, settings.data_dir)
        state.db.add_event("systeme", "Serveur Atlas démarré" + (" (DÉMO)" if settings.demo else ""), "ok")
        await state.adapter.probe()
        state.background = asyncio.create_task(_background_loop(state))
        try:
            yield
        finally:
            state.background.cancel()
            if state.turns.active and state.turns.active.run_id:
                await state.adapter.stop_run(state.turns.active.run_id)
            await state.adapter.close()
            state.db.close()

    app = FastAPI(title="Atlas", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)

    def atlas(request: Request) -> AtlasState:
        return request.app.state.atlas

    # -- sécurité : hôte, origine, authentification, en-têtes ---------------------
    def host_allowed(host_header: str | None) -> bool:
        if not host_header:
            return False
        host = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header.split("]")[0] + "]"
        host = host.strip("[]").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True
        if not settings.lan:
            return False
        try:
            ipaddress.ip_address(host)
            return True  # une IP littérale ne peut pas servir à une attaque par « rebinding » DNS
        except ValueError:
            name = socket.gethostname().lower()
            return host in {name, f"{name}.local"}

    def origin_allowed(origin: str | None, host_header: str | None) -> bool:
        if origin is None:
            return True  # navigation directe / client non-navigateur (le cookie reste exigé)
        match = re.match(r"^(https?)://([^/]+)$", origin)
        return bool(match) and match.group(2).lower() == (host_header or "").lower()

    @app.middleware("http")
    async def security(request: Request, call_next):
        if not host_allowed(request.headers.get("host")):
            return PlainTextResponse("Hôte non autorisé.", status_code=421)
        path = request.url.path
        if path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_allowed(
                    request.headers.get("origin"), request.headers.get("host")):
                return JSONResponse({"detail": "Origine non autorisée."}, status_code=403)
            length = int(request.headers.get("content-length") or 0)
            limit = settings.max_audio_bytes if path == "/api/voice/transcribe" else MAX_JSON_BYTES
            if length > limit:
                return JSONResponse({"detail": "Requête trop volumineuse."}, status_code=413)
            if path not in PUBLIC_PATHS:
                session = auth.check_session(atlas(request).db, request.cookies.get(auth.COOKIE_NAME))
                if not session:
                    return JSONResponse({"detail": "Connexion requise.", "code": "auth_required"}, status_code=401)
                request.state.device = session
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "media-src 'self' blob: data:; connect-src 'self' ws: wss:; font-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # -- public -----------------------------------------------------------------
    @app.get("/api/public/status")
    async def public_status(request: Request):
        st = atlas(request)
        logged = bool(auth.check_session(st.db, request.cookies.get(auth.COOKIE_NAME)))
        return {"app": "atlas", "version": __version__, "authenticated": logged, "demo": settings.demo,
                "https": settings.https, "lan": settings.lan}

    @app.post("/api/auth/login")
    async def login(body: LoginBody, request: Request):
        st = atlas(request)
        ip = request.client.host if request.client else "?"
        if not st.limiter.allowed(ip):
            raise HTTPException(429, "Trop de tentatives. Réessaie dans une minute.")
        await asyncio.sleep(0.3)
        if not auth.verify_code(st.db, body.code):
            st.limiter.record_failure(ip)
            st.db.add_event("securite", "Tentative de connexion refusée", "warning", detail=ip)
            raise HTTPException(401, "Code d'accès incorrect.")
        st.limiter.reset(ip)
        token = auth.create_session(st.db, body.device_label.strip() or "Appareil")
        st.db.add_event("securite", f"Nouvel appareil connecté : {body.device_label[:40]}", "ok", detail=ip)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_TTL_S, httponly=True, samesite="strict",
                        secure=settings.https, path="/")
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        auth.delete_session(atlas(request).db, request.cookies.get(auth.COOKIE_NAME))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE_NAME, path="/")
        return resp

    @app.get("/atlas-ca.crt")
    async def ca_certificate():
        path = settings.cert_dir / "atlas-ca.crt"
        if not path.is_file():
            raise HTTPException(404, "Aucun certificat local (mode réseau désactivé).")
        return FileResponse(path, media_type="application/x-x509-ca-cert", filename="atlas-ca.crt")

    # -- vue d'ensemble -----------------------------------------------------------------
    def network_info() -> dict[str, Any]:
        addresses = local_ip_addresses() if settings.lan else []
        return {"lan": settings.lan, "https": settings.https, "port": settings.port,
                "urls": [f"{settings.scheme}://{ip}:{settings.port}" for ip in addresses],
                "local_url": f"{settings.scheme}://localhost:{settings.port}",
                "ca_available": (settings.cert_dir / "atlas-ca.crt").is_file()}

    def voice_info(st: AtlasState) -> dict[str, Any]:
        return {"stt": st.stt.describe(), "tts": st.tts.describe(),
                "hermes_config": {"tts_provider": st.hermes_tts.get("provider"),
                                  "edge_voice": st.hermes_tts.get("edge_voice"),
                                  "stt_provider": st.hermes_tts.get("stt_provider")}}

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request):
        st = atlas(request)
        return {"version": __version__, "demo": settings.demo, "device": request.state.device["device_label"],
                "preferences": st.preferences(), "hermes": st.adapter.status.public(),
                "state": st.turns.snapshot(), "last_runtime": st.db.get_preferences().get("last_runtime"),
                "network": network_info(), "voice": voice_info(st),
                "schema_version": st.db.schema_version, "data_dir": str(settings.data_dir),
                "mcp": {"configured": bool(settings.mcp_token), "port": settings.mcp_port,
                        "last_seen": st.db.get_preferences().get("mcp_last_seen")}}

    @app.get("/api/hermes/status")
    async def hermes_status(request: Request, refresh: bool = False):
        st = atlas(request)
        if refresh:
            await st.adapter.probe()
            await st.hub.broadcast({"type": "hermes.status", "status": st.adapter.status.public()})
        return st.adapter.status.public()

    # -- conversations ------------------------------------------------------------------
    @app.get("/api/conversations")
    async def list_conversations(request: Request):
        return atlas(request).db.list_conversations()

    @app.post("/api/conversations")
    async def create_conversation(request: Request):
        st = atlas(request)
        conv = st.db.create_conversation(DEFAULT_TITLE)
        await st.hub.broadcast({"type": "conversations.changed"})
        return conv

    @app.patch("/api/conversations/{conv_id}")
    async def rename_conversation(conv_id: str, body: ConversationPatch, request: Request):
        st = atlas(request)
        if not st.db.get_conversation(conv_id):
            raise HTTPException(404, "Conversation introuvable.")
        st.db.update_conversation(conv_id, title=body.title.strip())
        await st.hub.broadcast({"type": "conversations.changed"})
        return st.db.get_conversation(conv_id)

    @app.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str, request: Request):
        st = atlas(request)
        if st.turns.active and st.turns.active.conversation_id == conv_id:
            raise HTTPException(409, "Une réponse est en cours dans cette conversation.")
        st.db.delete_conversation(conv_id)
        await st.hub.broadcast({"type": "conversations.changed"})
        return {"ok": True}

    @app.get("/api/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str, request: Request):
        st = atlas(request)
        conv = st.db.get_conversation(conv_id)
        if not conv:
            raise HTTPException(404, "Conversation introuvable.")
        messages = st.db.list_messages(conv_id)
        active = st.turns.active
        if active and active.conversation_id == conv_id:
            for msg in messages:
                if msg["id"] == active.assistant_message_id:
                    msg["content"] = active.content
        return {"conversation": conv, "messages": messages}

    @app.post("/api/conversations/{conv_id}/messages")
    async def send_message(conv_id: str, body: MessageBody, request: Request):
        st = atlas(request)
        if not REQUEST_ID_RE.match(body.request_id) or not DEVICE_ID_RE.match(body.device_id):
            raise HTTPException(400, "Identifiants de requête invalides.")
        if not body.content.strip():
            raise HTTPException(400, "Message vide.")
        try:
            result = await st.turns.submit(conv_id, body.request_id, body.content.strip(),
                                           device_id=body.device_id, kind=body.kind, want_tts=body.want_tts,
                                           note_ids=body.note_ids)
        except KeyError:
            raise HTTPException(404, "Conversation introuvable.")
        except Busy:
            raise HTTPException(409, detail={"code": "busy", "message": "Atlas est déjà occupé par une autre demande."})
        except Unavailable as exc:
            raise HTTPException(503, detail={"code": exc.kind, "message": str(exc)})
        except HermesError as exc:
            raise HTTPException(502, detail={"code": exc.kind, "message": str(exc)})
        return result

    @app.post("/api/turn/stop")
    async def stop_turn(request: Request):
        return await atlas(request).turns.stop()

    @app.post("/api/turn/approval")
    async def approve_turn(body: ApprovalBody, request: Request):
        try:
            return await atlas(request).turns.approve(body.choice)
        except HermesError as exc:
            raise HTTPException(502, detail={"code": exc.kind, "message": str(exc)})

    # -- compétences ---------------------------------------------------------------------
    @app.get("/api/skills/daily")
    async def daily_skill(request: Request):
        st = atlas(request)
        if not st.adapter.status.authenticated:
            return {"available": False, "reason": "Hermes n'est pas disponible."}
        now = time.monotonic()
        source = "api"
        if not st.skills_cache or now - st.skills_cache[0] > 300:
            try:
                st.skills_cache = (now, await st.adapter.list_skills())
            except Exception:
                # Repli : lecture seule des dossiers de compétences d'Hermes.
                local = scan_skill_dirs(settings.hermes_home / "skills")
                if not local:
                    return {"available": False, "reason": "Liste des compétences indisponible."}
                st.skills_cache = (now, local)
                source = "fichiers"
        skills = st.skills_cache[1]
        if settings.daily_skill:
            matches = [s for s in skills if s["name"] == settings.daily_skill]
        else:
            pattern = re.compile(r"(suivi|quotidien|daily)", re.IGNORECASE)
            matches = [s for s in skills if pattern.search(s["name"])]
            if not matches:
                matches = [s for s in skills if pattern.search(str(s.get("description", ""))[:80])]
        if len(matches) != 1:
            reason = ("Compétence de suivi quotidien introuvable." if not matches else
                      "Plusieurs compétences possibles : précise ATLAS_DAILY_SKILL dans atlas.env.")
            return {"available": False, "reason": reason, "candidates": [m["name"] for m in matches][:10]}
        skill = matches[0]
        return {"available": True, "name": skill["name"], "description": str(skill.get("description", ""))[:300],
                "source": source,
                "prompt": f"Utilise ta compétence « {skill['name']} » pour préparer ma journée avec moi."}

    # -- priorités -------------------------------------------------------------------------
    @app.get("/api/priorities")
    async def list_priorities(request: Request, day: str | None = None):
        st = atlas(request)
        day = _valid_day(day) if day else today_iso(st.preferences()["timezone"])
        return {"day": day, "items": st.db.list_priorities(day)}

    @app.post("/api/priorities")
    async def add_priority(body: PriorityBody, request: Request):
        st = atlas(request)
        day = _valid_day(body.day) if body.day else today_iso(st.preferences()["timezone"])
        with st.db.transaction() as db:
            existing = db.all("SELECT id FROM priorities WHERE day = ?", (day,))
            if len(existing) >= 3:
                raise HTTPException(409, "Trois priorités maximum par jour.")
            now = time.time()
            item = {"id": new_id("prio"), "title": body.title.strip(), "description": body.description.strip(),
                    "status": body.status, "day": day, "due": body.due or None, "position": len(existing),
                    "created_at": now, "updated_at": now}
            db.execute("INSERT INTO priorities (id, title, description, status, day, due, position, created_at, "
                       "updated_at) VALUES (?,?,?,?,?,?,?,?,?)", tuple(item.values()))
        await st.hub.broadcast({"type": "priorities.changed", "day": day})
        return item

    @app.patch("/api/priorities/{pid}")
    async def update_priority(pid: str, body: PriorityPatch, request: Request):
        st = atlas(request)
        item = st.db.get_priority(pid)
        if not item:
            raise HTTPException(404, "Priorité introuvable.")
        fields: dict[str, Any] = {}
        for key in ("title", "description", "status", "due"):
            value = getattr(body, key)
            if value is not None:
                fields[key] = value.strip() if isinstance(value, str) else value
        if body.clear_due:
            fields["due"] = None
        if fields:
            fields["updated_at"] = time.time()
            cols = ", ".join(f"{k} = ?" for k in fields)
            st.db.execute(f"UPDATE priorities SET {cols} WHERE id = ?", (*fields.values(), pid))
        await st.hub.broadcast({"type": "priorities.changed", "day": item["day"]})
        return st.db.get_priority(pid)

    @app.delete("/api/priorities/{pid}")
    async def delete_priority(pid: str, request: Request):
        st = atlas(request)
        item = st.db.get_priority(pid)
        if not item:
            raise HTTPException(404, "Priorité introuvable.")
        st.db.execute("DELETE FROM priorities WHERE id = ?", (pid,))
        await st.hub.broadcast({"type": "priorities.changed", "day": item["day"]})
        return {"ok": True}

    @app.post("/api/priorities/reorder")
    async def reorder_priorities(body: ReorderBody, request: Request):
        st = atlas(request)
        day = _valid_day(body.day)
        with st.db.transaction() as db:
            current = {r["id"] for r in db.all("SELECT id FROM priorities WHERE day = ?", (day,))}
            if set(body.ids) != current:
                raise HTTPException(409, "La liste a changé entre-temps. Recharge.")
            for index, pid in enumerate(body.ids):
                db.execute("UPDATE priorities SET position = ?, updated_at = ? WHERE id = ?", (index, time.time(), pid))
        await st.hub.broadcast({"type": "priorities.changed", "day": day})
        return {"ok": True}

    # -- tâches -------------------------------------------------------------------------
    @app.get("/api/tasks")
    async def list_tasks(request: Request, scope: str = "today"):
        st = atlas(request)
        if scope not in ("today", "upcoming", "done", "overdue", "all"):
            raise HTTPException(400, "Période inconnue.")
        tz = st.preferences()["timezone"]
        return {"today": today_iso(tz), "scope": scope, "items": st.db.list_tasks(scope, today_iso(tz))}

    @app.post("/api/tasks")
    async def add_task(body: TaskBody, request: Request):
        try:
            return await atlas(request).tasks.add(body.title, due_date=body.due_date, due_time=body.due_time,
                                                  notes=body.notes, by="user")
        except TaskError as exc:
            raise HTTPException(400, str(exc))

    @app.patch("/api/tasks/{tid}")
    async def update_task(tid: str, body: TaskPatch, request: Request):
        changes: dict[str, Any] = {"title": body.title, "notes": body.notes, "status": body.status}
        if body.due_date is not None or body.clear_date:
            changes["due_date"] = None if body.clear_date else body.due_date
        if body.due_time is not None or body.clear_time:
            changes["due_time"] = None if body.clear_time else body.due_time
        try:
            return await atlas(request).tasks.update(tid, by="user", **changes)
        except TaskError as exc:
            raise HTTPException(404 if "introuvable" in str(exc) else 400, str(exc))

    @app.delete("/api/tasks/{tid}")
    async def delete_task(tid: str, request: Request):
        try:
            await atlas(request).tasks.delete(tid, by="user")
        except TaskError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    # -- notes --------------------------------------------------------------------------
    @app.get("/api/notes")
    async def list_notes(request: Request):
        return atlas(request).db.list_notes()

    @app.post("/api/notes")
    async def add_note(body: NoteBody, request: Request):
        st = atlas(request)
        now = time.time()
        note = {"id": new_id("note"), "content": body.content, "created_at": now, "updated_at": now}
        st.db.execute("INSERT INTO notes (id, content, created_at, updated_at) VALUES (?,?,?,?)",
                      tuple(note.values()))
        await st.hub.broadcast({"type": "notes.changed", "origin": request.headers.get("x-atlas-device")})
        return note

    @app.patch("/api/notes/{nid}")
    async def update_note(nid: str, body: NotePatch, request: Request):
        st = atlas(request)
        with st.db.transaction() as db:
            note = db.one("SELECT * FROM notes WHERE id = ?", (nid,))
            if not note:
                raise HTTPException(404, "Note introuvable.")
            if body.expected_updated_at is not None and abs(note["updated_at"] - body.expected_updated_at) > 1e-6:
                raise HTTPException(409, detail={"code": "conflict", "message": "Cette note a été modifiée sur un autre appareil.",
                                                 "note": note})
            now = time.time()
            db.execute("UPDATE notes SET content = ?, updated_at = ? WHERE id = ?", (body.content, now, nid))
        await st.hub.broadcast({"type": "notes.changed", "origin": request.headers.get("x-atlas-device")})
        return st.db.get_note(nid)

    @app.delete("/api/notes/{nid}")
    async def delete_note(nid: str, request: Request):
        st = atlas(request)
        st.db.execute("DELETE FROM notes WHERE id = ?", (nid,))
        await st.hub.broadcast({"type": "notes.changed", "origin": request.headers.get("x-atlas-device")})
        return {"ok": True}

    # -- préférences ------------------------------------------------------------------------
    @app.get("/api/preferences")
    async def get_preferences(request: Request):
        return atlas(request).preferences()

    @app.patch("/api/preferences")
    async def patch_preferences(body: dict[str, Any], request: Request):
        st = atlas(request)
        for key, value in body.items():
            if key not in DEFAULT_PREFS:
                raise HTTPException(400, f"Réglage inconnu : {key}")
            if key == "timezone" and (not isinstance(value, str) or not valid_timezone(value)):
                raise HTTPException(400, "Fuseau horaire inconnu (format IANA, ex. Africa/Casablanca).")
            if key == "reduce_motion" and value not in ("system", "on", "off"):
                raise HTTPException(400, "Valeur invalide.")
            if key in ("autoplay_voice", "voice_confirm_before_send") and not isinstance(value, bool):
                raise HTTPException(400, "Valeur invalide.")
            st.db.set_preference(key, value)
        prefs = st.preferences()
        await st.hub.broadcast({"type": "preferences.changed", "preferences": prefs})
        return prefs

    # -- activité -----------------------------------------------------------------------
    @app.get("/api/activity")
    async def list_activity(request: Request, limit: int = 200):
        return atlas(request).db.list_events(max(1, min(limit, 1000)))

    @app.post("/api/activity/client")
    async def client_activity(body: ClientActivity, request: Request):
        category, label, status = CLIENT_ACTIVITY_LABELS[body.event]
        device = request.state.device["device_label"]
        await atlas(request).turns.activity(category, f"{label} ({device})", status)
        return {"ok": True}

    # -- voix ---------------------------------------------------------------------------
    @app.post("/api/voice/transcribe")
    async def transcribe(request: Request):
        st = atlas(request)
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in ("audio/wav", "audio/x-wav", "audio/wave"):
            raise HTTPException(415, "Format audio non accepté (WAV attendu).")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > settings.max_audio_bytes:
                raise HTTPException(413, "Enregistrement trop volumineux.")
        await st.turns.activity("voix", "Transcription en cours", "running")
        started = time.monotonic()
        try:
            result = await st.stt.transcribe(bytes(data))
        except STTError as exc:
            await st.turns.activity("voix", "Transcription impossible", "error", detail=str(exc))
            raise HTTPException(422, detail={"code": exc.kind, "message": str(exc)})
        finally:
            del data  # l'audio n'est jamais conservé
        await st.turns.activity("voix", "Transcription terminée", "ok",
                                duration_ms=int((time.monotonic() - started) * 1000),
                                detail=f"{result['duration_s']} s d'audio")
        return result

    @app.post("/api/voice/speak")
    async def speak(body: SpeakBody, request: Request):
        st = atlas(request)
        msg = st.db.get_message(body.message_id)
        if not msg or msg["role"] != "assistant":
            raise HTTPException(404, "Message introuvable.")
        content = msg["content"]
        active = st.turns.active
        if active and active.assistant_message_id == msg["id"]:
            content = active.content  # réponse encore en cours : lecture phrase par phrase
        text = content
        if body.text is not None:
            # Un extrait n'est lu que s'il provient réellement de la réponse d'Atlas.
            text = body.text.strip()
            if not text or text not in content:
                raise HTTPException(400, "Extrait introuvable dans la réponse.")
        if not text.strip():
            raise HTTPException(404, "Message vide.")
        started = time.monotonic()
        try:
            audio, mime = await st.tts.synthesize(text)
        except TTSError as exc:
            await st.turns.activity("voix", "Synthèse vocale impossible", "error", detail=str(exc))
            raise HTTPException(422, detail={"code": exc.kind, "message": str(exc)})
        if body.text is None or content.lstrip().startswith(text[:40]):
            await st.turns.activity("voix", "Synthèse vocale prête", "ok",
                                    duration_ms=int((time.monotonic() - started) * 1000))
        return Response(audio, media_type=mime, headers={"Cache-Control": "no-store"})

    # -- appareils, export ------------------------------------------------------------------
    @app.get("/api/devices")
    async def devices(request: Request):
        rows = atlas(request).db.all("SELECT device_label, created_at, last_seen FROM auth_sessions "
                                     "ORDER BY last_seen DESC")
        return rows

    @app.post("/api/devices/revoke-others")
    async def revoke_others(request: Request):
        st = atlas(request)
        mine = auth._token_hash(request.cookies.get(auth.COOKIE_NAME, ""))
        st.db.execute("DELETE FROM auth_sessions WHERE token_hash != ?", (mine,))
        await st.turns.activity("securite", "Autres appareils déconnectés", "ok")
        return {"ok": True}

    @app.get("/api/export")
    async def export(request: Request, format: str = "json"):
        st = atlas(request)
        notes, priorities = st.db.list_notes(), st.db.list_priorities()
        tasks = st.db.list_tasks("all", "")
        stamp = time.strftime("%Y%m%d-%H%M")
        if format == "md":
            lines = ["# Export Atlas", "", "## Priorités", ""]
            labels = {"todo": "à faire", "doing": "en cours", "done": "terminé"}
            for p in priorities:
                lines.append(f"- [{ 'x' if p['status'] == 'done' else ' ' }] {p['day']} — {p['title']} "
                             f"({labels.get(p['status'], p['status'])})" + (f" — {p['description']}" if p["description"] else ""))
            lines += ["", "## Tâches", ""]
            for t in tasks:
                lines.append(f"- [{'x' if t['status'] == 'done' else ' '}] {t['title']}"
                             + (f" — {t['due_date']}" if t["due_date"] else "") + (f" {t['due_time']}" if t["due_time"] else ""))
            lines += ["", "## Notes", ""]
            for n in notes:
                lines += [f"### Note du {time.strftime('%d/%m/%Y %H:%M', time.localtime(n['updated_at']))}", "",
                          n["content"], ""]
            return Response("\n".join(lines), media_type="text/markdown; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="atlas-export-{stamp}.md"'})
        payload = json.dumps({"exported_at": time.time(), "version": __version__, "notes": notes,
                              "priorities": priorities, "tasks": tasks}, ensure_ascii=False, indent=2)
        return Response(payload, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="atlas-export-{stamp}.json"'})

    # -- temps réel ---------------------------------------------------------------------
    @app.websocket("/api/ws")
    async def websocket(ws: WebSocket):
        st: AtlasState = ws.app.state.atlas
        host = ws.headers.get("host")
        if not host_allowed(host) or not origin_allowed(ws.headers.get("origin"), host) \
                or ws.headers.get("origin") is None:
            await ws.close(code=4403)
            return
        if not auth.check_session(st.db, ws.cookies.get(auth.COOKIE_NAME)):
            await ws.close(code=4401)
            return
        await ws.accept()
        st.hub.clients.add(ws)
        try:
            await ws.send_text(json.dumps({"type": "hello", "state": st.turns.snapshot(),
                                           "hermes": st.adapter.status.public()}, ensure_ascii=False))
            while True:
                message = await ws.receive_text()
                if message == "ping":
                    await ws.send_text('{"type":"pong"}')
        except WebSocketDisconnect:
            pass
        finally:
            st.hub.clients.discard(ws)

    # -- interface compilée ---------------------------------------------------------------
    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (STATIC_DIR / path).resolve()
        if path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
            return FileResponse(candidate, headers=headers)
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            return PlainTextResponse("Interface non compilée : lancer « npm run build » dans frontend/.", 503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


async def _background_loop(state: AtlasState) -> None:
    """Vérifie Hermes périodiquement — sans jamais appeler le modèle."""
    previous = json.dumps({k: v for k, v in state.adapter.status.public().items() if k != "checked_at"},
                          sort_keys=True, default=str)
    tick = 0
    while True:
        await asyncio.sleep(15)
        tick += 1
        try:
            state.stt.maybe_unload()
            # Toutes les 30 s si un navigateur est ouvert, toutes les 2 min sinon.
            if state.turns.active or (not state.hub.clients and tick % 8):
                continue
            if state.hub.clients and tick % 2:
                continue
            status = await state.adapter.probe()
            current = status.public()
            comparable = {k: v for k, v in current.items() if k != "checked_at"}
            snapshot = json.dumps(comparable, sort_keys=True, default=str)
            if snapshot != previous:
                previous = snapshot
                await state.hub.broadcast({"type": "hermes.status", "status": current})
                state.db.add_event("hermes", "Hermes disponible" if status.authenticated else "Hermes indisponible",
                                   "ok" if status.authenticated else "error", detail=redact(status.error))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Boucle de surveillance")
