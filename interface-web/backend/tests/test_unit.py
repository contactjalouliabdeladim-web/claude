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
from atlas_server.db import MIGRATIONS, Database
from atlas_server.hermes_adapter import HermesAdapter, HermesError, classify_failure_text, iter_sse
from atlas_server.util import redact, valid_timezone
from atlas_server.voice.hermes_config import read_simple_yaml_value
from atlas_server.voice.stt import STTError, STTService, decode_wav, wav_duration
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
    assert db.schema_version == len(MIGRATIONS)
    conv = db.create_conversation()
    msg = db.add_message(conv["id"], "assistant", "", status="streaming")
    assert db.mark_interrupted_messages() == 1
    assert db.get_message(msg["id"])["status"] == "interrupted"
    db.close()
    db2 = Database(tmp_path / "a.db")  # réouverture : pas de nouvelle migration
    assert db2.schema_version == len(MIGRATIONS) and len(db2.list_conversations()) == 1


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


def test_local_transcription_uses_french_business_context():
    captured = {}

    class Model:
        def transcribe(self, samples, **kwargs):
            captured.update(kwargs)
            return [type("Segment", (), {"text": "Bonjour Atlas"})()], None

    service = STTService("local_whisper", "base")
    service._model = Model()
    assert service._transcribe_local(make_wav()) == "Bonjour Atlas"
    assert captured["language"] == "fr"
    assert "Atlas" in captured["initial_prompt"]
    assert "dirhams" in captured["initial_prompt"]


def test_local_stt_warm_preloads_model_and_resets_idle_timer(monkeypatch):
    service = STTService("local_whisper", "base")
    loaded = []
    monkeypatch.setattr(service, "local_available", lambda: True)
    monkeypatch.setattr(service, "_load", lambda: loaded.append(True))
    service.warm()
    assert loaded == [True]
    assert service._last_used > 0


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


def test_finance_api_records_currency_specific_cashflow(client):
    income = client.post("/api/finance/transactions", json={
        "kind": "income", "amount_cents": 125000, "currency": "MAD", "date": "2026-10-14",
        "client": "Rome Arab Tours", "category": "Prestation", "note": "Acompte",
    })
    assert income.status_code == 200
    expense = client.post("/api/finance/transactions", json={
        "kind": "expense", "amount_cents": 25000, "currency": "MAD", "date": "2026-10-14",
        "client": "", "category": "Outils", "note": "Abonnement",
    })
    assert expense.status_code == 200
    euro = client.post("/api/finance/transactions", json={
        "kind": "income", "amount_cents": 5000, "currency": "EUR", "date": "2026-10-14",
    })
    assert euro.status_code == 200
    overview = client.get("/api/finance/overview?date=2026-10-14").json()
    assert overview["today"]["MAD"] == {"income_cents": 125000, "expense_cents": 25000, "net_cents": 100000}
    assert overview["today"]["EUR"]["income_cents"] == 5000
    assert len(overview["transactions"]) == 3
    assert client.post("/api/finance/transactions", json={
        "kind": "income", "amount_cents": 100, "currency": "USD", "date": "2026-10-14",
    }).status_code == 422


def test_finance_api_rejects_invalid_dates_and_amounts(client):
    payload = {"kind": "income", "amount_cents": 100, "currency": "MAD", "date": "2026-02-30"}
    assert client.post("/api/finance/transactions", json=payload).status_code == 400
    payload["date"] = "2026-10-14"
    payload["amount_cents"] = 0
    assert client.post("/api/finance/transactions", json=payload).status_code == 422


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


# -- tâches et outils d'Atlas (MCP) --------------------------------------------------------------
def test_task_parsing():
    from atlas_server.tasks import TaskError, parse_due_date, parse_due_time
    assert parse_due_time("15h") == "15:00" and parse_due_time("9h30") == "09:30" and parse_due_time("") is None
    assert len(parse_due_date("demain", "Africa/Casablanca")) == 10
    with pytest.raises(TaskError):
        parse_due_time("25:99")
    with pytest.raises(TaskError):
        parse_due_date("bientôt", "UTC")


def test_tasks_api(client):
    t = client.post("/api/tasks", json={"title": "Payer la facture", "due_date": "demain", "due_time": "10h"}).json()
    assert t["due_time"] == "10:00" and t["source"] == "user"
    assert client.get("/api/tasks?scope=upcoming").json()["items"][0]["id"] == t["id"]
    done = client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["completed_at"]
    assert client.get("/api/tasks?scope=done").json()["items"][0]["id"] == t["id"]
    assert client.patch(f"/api/tasks/{t['id']}", json={"due_time": "99:99"}).status_code == 400
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 200


def test_mcp_stdio_roundtrip(tmp_path, monkeypatch):
    """Le serveur d'outils lancé par Hermes : JSON-RPC ligne par ligne, écriture réelle en base."""
    import subprocess
    import sys
    ensure_env_file(tmp_path)
    Database(tmp_path / "atlas.db").close()
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "tache_ajouter", "arguments": {"titre": "Appeler le comptable", "heure": "15h"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "tache_ajouter", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "journee", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "finance_ajouter", "arguments": {
            "type": "income", "montant": 125.50, "devise": "MAD", "client": "Rome Arab Tours"}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "finance_resume", "arguments": {"periode": "aujourdhui"}}},
    ]
    env = dict(__import__("os").environ, ATLAS_DATA_DIR=str(tmp_path), ATLAS_DEMO="0")
    proc = subprocess.run([sys.executable, "-m", "atlas_server", "mcp-stdio"], input="\n".join(json.dumps(m) for m in lines) + "\n",
                          capture_output=True, text=True, env=env, cwd=Path(__file__).resolve().parents[1], timeout=60)
    replies = [json.loads(l) for l in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, 7]  # pas de réponse aux notifications, rien d'autre sur stdout
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert len(replies[1]["result"]["tools"]) == 17
    added = replies[2]["result"]["structuredContent"]
    assert added["ok"] and added["tache"]["heure"] == "15:00" and added["tache"]["ajoutee_par"] == "Atlas"
    assert replies[3]["result"]["isError"] is True
    assert replies[4]["result"]["structuredContent"]["taches_du_jour"][0]["titre"] == "Appeler le comptable"
    assert replies[5]["result"]["structuredContent"]["transaction"]["amount_cents"] == 12550
    assert replies[6]["result"]["structuredContent"]["totaux"]["MAD"]["income_cents"] == 12550
    db = Database(tmp_path / "atlas.db")
    assert db.list_tasks("all", "")[0]["source"] == "atlas"
    assert db.list_finance_transactions()[0]["client"] == "Rome Arab Tours"
    assert any("Atlas a ajouté la tâche" in e["label"] for e in db.list_events())


def test_mcp_shared_pages_between_agents(tmp_path):
    """Pages partagées : Claude écrit, Atlas complète et relit la même base ; l'auteur est tracé."""
    import os
    import subprocess
    import sys
    ensure_env_file(tmp_path)

    def call(agent, *calls):
        msgs = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": a}}
                for i, (n, a) in enumerate(calls, 1)]
        env = dict(os.environ, ATLAS_DATA_DIR=str(tmp_path), ATLAS_DEMO="0", ATLAS_MCP_AGENT=agent)
        proc = subprocess.run([sys.executable, "-m", "atlas_server", "mcp-stdio"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                              capture_output=True, text=True, encoding="utf-8", env=env, cwd=Path(__file__).resolve().parents[1], timeout=60)
        return [json.loads(l)["result"] for l in proc.stdout.splitlines()]

    r = call("claude", ("page_ecrire", {"titre": "Projet Scaleo", "contenu": "# Objectifs\n- site", "etiquettes": "projet, scaleo"}),
             ("tache_ajouter", {"titre": "Relire le site"}))
    assert r[0]["structuredContent"]["action"] == "créée"
    r = call("atlas", ("page_ecrire", {"titre": "projet scaleo", "contenu": "- devis", "mode": "ajouter"}),
             ("page_lire", {"titre": "PROJET SCALEO"}), ("pages_chercher", {"texte": "devis"}), ("page_lire", {"titre": "absente"}))
    page = r[1]["structuredContent"]["page"]
    assert r[0]["structuredContent"]["action"] == "mise à jour"
    assert page["contenu"] == "# Objectifs\n- site\n\n- devis" and page["etiquettes"] == "projet, scaleo"
    assert page["creee_par"] == "claude" and page["modifiee_par"] == "atlas"
    assert r[2]["structuredContent"]["nombre"] == 1 and r[3]["isError"] is True
    db = Database(tmp_path / "atlas.db")
    labels = [e["label"] for e in db.list_events()]
    assert any(l.startswith("Claude a créé la page") for l in labels)
    assert any(l.startswith("Claude a ajouté la tâche") for l in labels)
    assert any(l.startswith("Atlas a mis à jour la page") for l in labels)
    assert "mcp_last_seen_claude" in db.get_preferences()  # contact de Claude suivi à part de celui d'Hermes
