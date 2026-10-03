"""Tests unitaires d'Atlas (aucun Hermes requis). Lancer : python -m pytest tests -q"""

from __future__ import annotations

import asyncio
import io
import json
import math
import struct
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas_server import auth
from atlas_server.app import create_app, scan_skill_dirs
from atlas_server.certs import ensure_server_certificate
from atlas_server.config import ensure_env_file, load_settings, parse_env_file, update_env_file
from atlas_server.db import Database
from atlas_server.hermes_adapter import HermesAdapter, HermesError, classify_failure_text, iter_sse
from atlas_server.util import redact, valid_timezone
from atlas_server.voice.hermes_config import read_simple_yaml_value
from atlas_server.voice.stt import STTError, decode_wav, wav_duration
from atlas_server.voice.tts import text_for_speech


def make_wav(seconds=1.0, rate=48000, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) * channels for i in range(int(rate * seconds)))
        w.writeframes(frames)
    return buf.getvalue()


# -- configuration ------------------------------------------------------------------
def test_env_file_roundtrip(tmp_path):
    path, created = ensure_env_file(tmp_path)
    assert created and len(parse_env_file(path)["HERMES_API_KEY"]) == 64
    update_env_file(path, {"ATLAS_LAN": "1", "NEW_KEY": "x"})
    values = parse_env_file(path)
    assert values["ATLAS_LAN"] == "1" and values["NEW_KEY"] == "x"
    assert "# Configuration d'Atlas" in path.read_text(encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.lan and settings.https and settings.host == "0.0.0.0"


def test_hermes_yaml_reader(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: openai-codex\ntts:\n  provider: edge  # commentaire\n  edge:\n"
                   "    voice: \"fr-FR-HenriNeural\"\nstt:\n  enabled: true\n", encoding="utf-8")
    assert read_simple_yaml_value(cfg, "tts.edge.voice") == "fr-FR-HenriNeural"
    assert read_simple_yaml_value(cfg, "tts.provider") == "edge"
    assert read_simple_yaml_value(cfg, "stt.provider") is None


# -- base de données ----------------------------------------------------------------
def test_db_migration_and_messages(tmp_path):
    db = Database(tmp_path / "a.db")
    assert db.schema_version == 1
    conv = db.create_conversation()
    msg = db.add_message(conv["id"], "assistant", "", status="streaming")
    assert db.mark_interrupted_messages() == 1
    assert db.get_message(msg["id"])["status"] == "interrupted"
    db.close()
    db2 = Database(tmp_path / "a.db")  # réouverture : pas de nouvelle migration
    assert db2.schema_version == 1 and len(db2.list_conversations()) == 1


def test_events_are_capped(tmp_path):
    db = Database(tmp_path / "e.db")
    for i in range(3300):
        db.add_event("test", f"e{i}", "ok")
    count = db.one("SELECT COUNT(*) AS n FROM events")["n"]
    assert count <= 3200


# -- authentification -----------------------------------------------------------------
def test_access_code_and_sessions(tmp_path):
    db = Database(tmp_path / "auth.db")
    code = auth.ensure_access_code(db, tmp_path)
    assert code and auth.ensure_access_code(db, tmp_path) is None
    assert auth.verify_code(db, code.lower().replace("-", " "))
    assert not auth.verify_code(db, "AAAA-BBBB-CCCC")
    assert code not in json.dumps(db.all("SELECT * FROM secrets"))  # jamais stocké en clair
    token = auth.create_session(db, "Mac")
    assert auth.check_session(db, token)["device_label"] == "Mac"
    auth.set_access_code(db, tmp_path)  # nouveau code : tous les appareils déconnectés
    assert auth.check_session(db, token) is None


def test_rate_limiter():
    lim = auth.LoginRateLimiter()
    for _ in range(5):
        assert lim.allowed("1.2.3.4")
        lim.record_failure("1.2.3.4")
    assert not lim.allowed("1.2.3.4") and lim.allowed("5.6.7.8")


# -- utilitaires ----------------------------------------------------------------------
def test_redact():
    text = "API_SERVER_KEY=abcdef123456 et Bearer abcdefghijklmnop et sk-proj-1234567890abc"
    out = redact(text)
    assert "abcdef123456" not in out and "abcdefghijklmnop" not in out and "1234567890abc" not in out


def test_timezone_validation():
    assert valid_timezone("Africa/Casablanca") and not valid_timezone("Mars/Olympus")


def test_failure_classification():
    assert classify_failure_text("HTTP 429: You exceeded your current quota") == "quota"
    assert classify_failure_text("401 Unauthorized") == "provider_auth"
    assert classify_failure_text("boom") == "provider"


def test_text_for_speech():
    speech, truncated = text_for_speech("# Titre\n**Gras** et `code`\n```\nx=1\n```\nVoir https://exemple.fr")
    assert "**" not in speech and "x=1" not in speech and "https" not in speech and not truncated
    long_text, truncated = text_for_speech("Phrase. " * 600)
    assert truncated and len(long_text) < 2700


# -- audio ----------------------------------------------------------------------------
def test_wav_decode_resamples_to_16k():
    samples, duration = decode_wav(make_wav(1.0, rate=48000, channels=2))
    assert abs(duration - 1.0) < 0.01 and abs(len(samples) - 16000) < 5
    assert samples.dtype.name == "float32" and abs(samples).max() <= 1.0


def test_wav_errors():
    with pytest.raises(STTError):
        wav_duration(b"not a wav")


def test_browser_recorded_wav_if_available():
    sample = Path("/tmp/atlas-shots/micro.wav")
    if not sample.exists():
        pytest.skip("pas d'enregistrement navigateur disponible")
    samples, duration = decode_wav(sample.read_bytes())
    assert duration > 1 and len(samples) > 16000


# -- certificats ------------------------------------------------------------------------
def test_certificates(tmp_path):
    from cryptography import x509
    crt, key, regenerated = ensure_server_certificate(tmp_path)
    assert regenerated and crt.exists() and key.exists() and (tmp_path / "atlas-ca.crt").exists()
    cert = x509.load_pem_x509_certificate(crt.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days <= 825  # limite macOS
    _, _, again = ensure_server_certificate(tmp_path)
    assert not again


# -- compétences ---------------------------------------------------------------------------
def test_scan_skill_dirs(tmp_path):
    d = tmp_path / "productivity" / "suivi-quotidien"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: suivi-quotidien\ndescription: Bilan du jour\n---\n", encoding="utf-8")
    assert scan_skill_dirs(tmp_path) == [{"name": "suivi-quotidien", "description": "Bilan du jour"}]


# -- adaptateur Hermes (transport simulé) --------------------------------------------------------
def sse(*events):
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events) + ": keepalive\n\n"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_iter_sse_parses_named_events_and_comments():
    body = ": keepalive\n\nevent: a\ndata: {\"x\": 1}\n\ndata: [DONE]\n\n"

    async def go():
        resp = httpx.Response(200, content=body.encode())
        return [e async for e in iter_sse(resp)]
    assert run(go()) == [("a", {"x": 1}), ("done", {})]


def caps_handler(features):
    def handler(request: httpx.Request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/capabilities":
            if request.headers.get("authorization") != "Bearer k":
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"model": "hermes-agent", "features": features})
        if request.url.path == "/api/sessions":
            return httpx.Response(201, json={"session": {"id": "sess_1"}})
        if request.url.path == "/api/sessions/sess_1/chat/stream":
            assert json.loads(request.content)["message"] == "salut"
            return httpx.Response(200, text=sse(
                ("run.started", {"run_id": "run_1"}), ("assistant.delta", {"delta": "Bon"}),
                ("tool.started", {"tool_name": "memory", "preview": "lecture"}),
                ("tool.started", {"tool_name": "_thinking"}),
                ("tool.completed", {"tool_name": "memory"}), ("assistant.delta", {"delta": "jour"}),
                ("assistant.completed", {"content": "Bonjour", "session_id": "sess_2"}),
                ("run.completed", {"usage": {"total_tokens": 3}, "runtime": {"model": "m"}}), ("done", {})))
        if request.url.path == "/v1/chat/completions":
            assert request.headers["X-Hermes-Session-Id"] == "atlas_s"
            chunks = [{"choices": [{"delta": {"content": "Sa"}}]}, {"choices": [{"delta": {"content": "lut"}, "finish_reason": "stop"}]}]
            body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"X-Hermes-Session-Id": "atlas_s"})
        return httpx.Response(404, json={})
    return handler


def test_adapter_sessions_mode():
    transport = httpx.MockTransport(caps_handler({"session_chat_streaming": True, "run_stop": True}))

    async def go():
        a = HermesAdapter("http://h", "k", transport=transport)
        status = await a.probe()
        assert status.mode == "sessions" and status.can_stop
        sid = await a.create_session("Titre")
        events = []
        result = await a.stream_turn(sid, "salut", on_event=events.append)
        return events, result
    events, result = run(go())
    types = [e["type"] for e in events]
    assert types == ["run_started", "delta", "tool_started", "tool_completed", "delta"]  # _thinking filtré
    assert result["status"] == "completed" and result["content"] == "Bonjour"
    assert result["session_id"] == "sess_2" and result["run_id"] == "run_1"


def test_adapter_fallback_chat_completions():
    transport = httpx.MockTransport(caps_handler({"session_chat_streaming": False, "chat_completions": True}))

    async def go():
        a = HermesAdapter("http://h", "k", transport=transport)
        await a.probe()
        assert a.status.mode == "chat_completions" and not a.status.can_stop
        events = []
        return await a.stream_turn("atlas_s", "salut", on_event=events.append), events
    result, events = run(go())
    assert result["content"] == "Salut" and [e["type"] for e in events][-2:] == ["delta", "delta"]


def test_adapter_bad_key_and_unreachable():
    transport = httpx.MockTransport(caps_handler({}))

    async def go():
        bad = await HermesAdapter("http://h", "mauvaise", transport=transport).probe()

        def down(_):
            raise httpx.ConnectError("refusé")
        off = await HermesAdapter("http://h", "k", transport=httpx.MockTransport(down)).probe()
        return bad, off
    bad, off = run(go())
    assert bad.reachable and not bad.authenticated and bad.error_kind == "auth"
    assert not off.reachable and off.error_kind == "unreachable"


def test_adapter_stream_lost():
    def handler(request):
        if request.url.path == "/api/sessions/s/chat/stream":
            raise httpx.ReadTimeout("coupé")
        return caps_handler({"session_chat_streaming": True})(request)

    async def go():
        a = HermesAdapter("http://h", "k", transport=httpx.MockTransport(handler))
        await a.probe()
        await a.stream_turn("s", "x", on_event=lambda e: None)
    with pytest.raises(HermesError) as info:
        run(go())
    assert info.value.kind == "stream_lost"


# -- API (mode démonstration) ------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_DEMO", "1")
    ensure_env_file(tmp_path)
    settings = load_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as c:
        code = (tmp_path / "CODE-ACCES.txt").read_text(encoding="utf-8").split()[-1]
        r = c.post("/api/auth/login", json={"code": code}, headers={"Origin": "http://localhost"})
        assert r.status_code == 200
        c.headers.update({"Origin": "http://localhost"})
        yield c


def test_priorities_rules(client):
    for i in range(3):
        assert client.post("/api/priorities", json={"title": f"P{i}"}).status_code == 200
    assert client.post("/api/priorities", json={"title": "P4"}).status_code == 409
    data = client.get("/api/priorities").json()
    ids = [p["id"] for p in data["items"]]
    assert client.post("/api/priorities/reorder", json={"day": data["day"], "ids": ids[::-1]}).status_code == 200
    assert [p["id"] for p in client.get("/api/priorities").json()["items"]] == ids[::-1]
    assert client.patch(f"/api/priorities/{ids[0]}", json={"status": "fini"}).status_code == 422
    assert client.patch(f"/api/priorities/{ids[0]}", json={"status": "done"}).json()["status"] == "done"


def test_notes_conflict(client):
    note = client.post("/api/notes", json={"content": "a"}).json()
    ok = client.patch(f"/api/notes/{note['id']}", json={"content": "b", "expected_updated_at": note["updated_at"]})
    assert ok.status_code == 200
    stale = client.patch(f"/api/notes/{note['id']}", json={"content": "c", "expected_updated_at": note["updated_at"]})
    assert stale.status_code == 409


def test_preferences_validation(client):
    assert client.patch("/api/preferences", json={"timezone": "Europe/Paris"}).json()["timezone"] == "Europe/Paris"
    assert client.patch("/api/preferences", json={"timezone": "Nulle/Part"}).status_code == 400
    assert client.patch("/api/preferences", json={"model": "x"}).status_code == 400


def test_export_and_path_traversal(client):
    client.post("/api/notes", json={"content": "Exportée"})
    assert "Exportée" in client.get("/api/export?format=md").text
    assert client.get("/../../atlas.env").status_code in (200, 404, 503)
    assert "HERMES_API_KEY" not in client.get("/..%2F..%2Fdonnees%2Fatlas.env").text


def test_message_validation(client):
    conv = client.post("/api/conversations").json()["id"]
    bad = client.post(f"/api/conversations/{conv}/messages", json={"request_id": "x", "content": "a", "device_id": "dev_abcdef"})
    assert bad.status_code == 422
    big = client.post(f"/api/conversations/{conv}/messages",
                      json={"request_id": "req_12345678", "content": "a" * 20001, "device_id": "dev_abcdef"})
    assert big.status_code == 422


def test_transcribe_rejects_wrong_format(client):
    r = client.post("/api/voice/transcribe", content=b"abc", headers={"Content-Type": "audio/webm"})
    assert r.status_code == 415
