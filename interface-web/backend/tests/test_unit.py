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
    assert len(replies[1]["result"]["tools"]) == 20
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


# -- cockpit 120K ---------------------------------------------------------------------
from atlas_server.cockpit import COCKPIT_DEFAULTS, CockpitError, compute, validate_setting  # noqa: E402


def totals(mad_net_dh=0, eur_net=0):
    return {"MAD": {"net_cents": int(mad_net_dh * 100)}, "EUR": {"net_cents": int(eur_net * 100)}}


def test_cockpit_ahead_with_eur_conversion():
    # 125 jours (29/09 → 31/01), 16 écoulés le 14/10 ; 30 000 DH + 400 € × 10,5 = 34 200 DH
    c = compute(totals(30000, 400), dict(COCKPIT_DEFAULTS, cockpit_eur_mad=10.5), "2026-10-14")
    assert (c["jours_total"], c["jours_ecoules"], c["jours_restants"]) == (125, 16, 110)
    assert c["cumul_net_mad"] == 34200 and c["reste_mad"] == 85800 and c["pourcentage"] == 28.5
    assert c["rythme_necessaire_semaine_mad"] == 5460            # 85 800 / (110 / 7)
    assert c["rythme_reel_semaine_mad"] == 14962.5               # 34 200 / (16 / 7)
    assert c["attendu_a_date_mad"] == 15360 and c["ecart_mad"] == 18840 and c["ecart_jours"] == 19.6
    assert c["etat"] == "dans_les_temps"
    assert c["resume"] == "34 200 / 120 000 DH · il te faut 5 460 DH/semaine · avance de 20 jours"


def test_cockpit_delay_levels():
    light = compute(totals(10000), COCKPIT_DEFAULTS, "2026-10-14")   # 15 360 attendus → −5 360 DH ≈ −5,6 j
    assert light["etat"] == "leger_retard" and light["ecart_jours"] == -5.6 and "retard de 6 jours" in light["resume"]
    heavy = compute(totals(0), COCKPIT_DEFAULTS, "2026-10-14")
    assert heavy["etat"] == "gros_retard" and heavy["ecart_jours"] == -16.0
    on_time = compute(totals(15000), COCKPIT_DEFAULTS, "2026-10-14")  # −360 DH ≈ −0,4 j : dans les temps
    assert on_time["etat"] == "dans_les_temps" and on_time["resume"].endswith("pile dans les temps")


def test_cockpit_prior_amount_counts_in_total_but_not_in_real_pace():
    c = compute(totals(7000), dict(COCKPIT_DEFAULTS, cockpit_prior_mad=10000), "2026-10-05")  # 7 jours écoulés
    assert c["cumul_net_mad"] == 17000 and c["rythme_reel_semaine_mad"] == 7000


def test_cockpit_edges_before_start_after_end_and_reached():
    before = compute(totals(0), COCKPIT_DEFAULTS, "2026-09-01")
    assert before["jours_ecoules"] == 0 and before["rythme_reel_semaine_mad"] is None and before["jours_restants"] == 125
    after = compute(totals(50000), COCKPIT_DEFAULTS, "2027-02-10")
    assert after["jours_restants"] == 0 and after["rythme_necessaire_semaine_mad"] is None and after["etat"] == "gros_retard"
    done = compute(totals(125000), COCKPIT_DEFAULTS, "2026-12-01")
    assert done["etat"] == "atteint" and done["reste_mad"] == 0 and done["resume"].endswith("objectif atteint")
    with pytest.raises(CockpitError):
        compute(totals(0), dict(COCKPIT_DEFAULTS, cockpit_end="2026-01-01"), "2026-10-14")


def test_cockpit_setting_validation():
    assert validate_setting("cockpit_start", "2026-09-29") == "2026-09-29"
    for key, bad in (("cockpit_start", "29/09"), ("cockpit_goal_mad", 0), ("cockpit_eur_mad", -1),
                     ("cockpit_goal_mad", True), ("cockpit_prior_mad", "x")):
        with pytest.raises(CockpitError):
            validate_setting(key, bad)


def test_cockpit_api_uses_existing_finances_and_preferences(client):
    client.patch("/api/preferences", json={"cockpit_eur_mad": 11, "cockpit_start": "2026-01-01", "cockpit_end": "2026-12-31"})
    assert client.patch("/api/preferences", json={"cockpit_eur_mad": 0}).status_code == 400
    for kind, cents, cur in (("income", 500000, "MAD"), ("expense", 100000, "MAD"), ("income", 10000, "EUR")):
        assert client.post("/api/finance/transactions", json={"kind": kind, "amount_cents": cents, "currency": cur,
                                                              "date": "2026-03-01"}).status_code == 200
    data = client.get("/api/cockpit").json()
    assert data["cumul_net_mad"] == 4000 + 100 * 11 and data["taux_eur_mad"] == 11 and data["action_du_jour"] is None


def test_mcp_cockpit_and_money_action(tmp_path):
    import os
    import subprocess
    import sys
    ensure_env_file(tmp_path)
    msgs = [("finance_ajouter", {"type": "income", "montant": 3000, "devise": "MAD", "client": "Kick-Off TV"}),
            ("action_du_jour_definir", {"action": "Relancer Ecologia pour le devis", "pourquoi": "devis prêt depuis 3 jours"}),
            ("cockpit", {}), ("action_du_jour_definir", {"action": "  "})]
    lines = [json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": a}})
             for i, (n, a) in enumerate(msgs, 1)]
    env = dict(os.environ, ATLAS_DATA_DIR=str(tmp_path), ATLAS_DEMO="0", ATLAS_MCP_AGENT="atlas")
    proc = subprocess.run([sys.executable, "-m", "atlas_server", "mcp-stdio"], input="\n".join(lines) + "\n",
                          capture_output=True, text=True, encoding="utf-8", env=env,
                          cwd=Path(__file__).resolve().parents[1], timeout=60)
    r = [json.loads(l)["result"] for l in proc.stdout.splitlines()]
    cockpit = r[2]["structuredContent"]
    assert cockpit["action_du_jour"]["action"] == "Relancer Ecologia pour le devis" and cockpit["action_du_jour"]["par"] == "atlas"
    assert cockpit["encaissements_par_client"][0]["client"] == "Kick-Off TV"
    assert cockpit["objectif_mad"] == 120000 and "resume" in cockpit
    assert r[3]["isError"] is True


# -- appels d'Atlas ----------------------------------------------------------------------
from datetime import datetime as _dt  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from atlas_server.calls import CallError, CallScheduler, call_context, is_goodnight, validate_setting as call_setting  # noqa: E402

CASA = ZoneInfo("Africa/Casablanca")


def ts(text):
    return _dt.fromisoformat(text).replace(tzinfo=CASA).timestamp()


def make_scheduler(tmp_path, now_text, **prefs):
    db = Database(tmp_path / "atlas.db")
    for k, v in prefs.items():
        db.set_preference(k, v)
    sent = []
    clock = {"now": ts(now_text)}
    sch = CallScheduler(db, lambda: "Africa/Casablanca", send=lambda payload, s: sent.append(payload),
                        now=lambda: clock["now"])
    return sch, sent, clock


def test_morning_call_depends_on_bedtime(tmp_path):
    cases = [("2026-10-13T23:10", "05:30"), ("2026-10-13T23:45", "08:00"), ("2026-10-14T00:30", "08:00"),
             (None, "08:00"), ("2026-10-10T22:00", "08:00")]
    for bed, expected in cases:
        prefs = {"last_bedtime": {"ts": ts(bed)}} if bed else {}
        sch, _, _ = make_scheduler(tmp_path / (bed or "none").replace(":", ""), "2026-10-14T04:00", **prefs)
        morning = sch.status()["aujourdhui"][0]
        assert (morning["slot"], morning["time"]) == ("morning", expected), bed


def test_goodnight_sets_next_morning_call(tmp_path):
    sch, _, clock = make_scheduler(tmp_path, "2026-10-13T22:55")
    info = sch.note_bedtime()
    assert info == {"coucher": "22:55", "jour_appel": "2026-10-14", "appel_du_matin": "05:30",
                    "raison": "couché à 22:55, avant 23:30"}
    clock["now"] = ts("2026-10-14T00:40")  # coucher après minuit → appel tardif le jour même
    assert sch.note_bedtime()["appel_du_matin"] == "08:00"


def test_ring_once_then_single_retry_after_15_minutes(tmp_path):
    sch, sent, clock = make_scheduler(tmp_path, "2026-10-14T07:59", call_afternoon_enabled=False,
                                      call_evening_enabled=False, ntfy_topic="atlas-secret-topic-123456",
                                      call_public_url="https://hp.tailnet.ts.net")
    assert sch.tick() == []
    clock["now"] = ts("2026-10-14T08:00:10")
    assert sch.tick() == ["morning"] and sch.tick() == []
    assert sent[0]["topic"] == "atlas-secret-topic-123456" and sent[0]["priority"] == 5
    assert sent[0]["title"] == "Atlas t'appelle" and sent[0]["click"] == "https://hp.tailnet.ts.net/appel?slot=morning"
    clock["now"] = ts("2026-10-14T08:14")
    assert sch.tick() == []
    clock["now"] = ts("2026-10-14T08:15:15")
    assert sch.tick() == ["morning:relance"] and "relance" in sent[1]["message"]
    for later in ("2026-10-14T08:31", "2026-10-14T08:50", "2026-10-14T09:20"):
        clock["now"] = ts(later)
        assert sch.tick() == []  # jamais plus d'une relance
    assert len(sent) == 2


def test_answered_call_is_not_retried_and_disabled_slots_never_ring(tmp_path):
    sch, sent, clock = make_scheduler(tmp_path, "2026-10-14T14:00:05", call_evening_enabled=False)
    sch.tick()  # l'appel du matin (08:00) est passé depuis plus d'une heure : pas de sonnerie tardive
    assert [p["click"].split("=")[1] for p in sent] == ["afternoon"]
    sch.answered("afternoon")
    clock["now"] = ts("2026-10-14T14:20")
    assert sch.tick() == []
    clock["now"] = ts("2026-10-14T20:00:30")
    assert sch.tick() == [] and len(sent) == 1
    sch2, sent2, _ = make_scheduler(tmp_path / "off", "2026-10-14T08:00:05", calls_enabled=False)
    assert sch2.tick() == [] and sch2.status()["aujourdhui"] == []


def test_ring_failure_is_logged_without_retry_storm(tmp_path):
    db = Database(tmp_path / "atlas.db")
    calls = []

    def broken(payload, s):
        calls.append(1)
        raise OSError("réseau coupé")

    clock = {"now": ts("2026-10-14T08:00:05")}
    sch = CallScheduler(db, lambda: "Africa/Casablanca", send=broken, now=lambda: clock["now"])
    sch.tick(); sch.tick()
    assert len(calls) == 1 and "réseau" in sch.entry("2026-10-14", "morning")["error"]


def test_goodnight_detection_and_call_context():
    assert is_goodnight("Bonne nuit Atlas !") and is_goodnight("allez, bonne  nuit") and not is_goodnight("nuit blanche")
    assert any("2 à 3 phrases" in p for p in call_context("live", False, False))
    morning = " ".join(call_context("morning", True, False))
    assert "mcp_atlas_cockpit" in morning and "mcp_atlas_priorite_ajouter" in morning and "mcp_atlas_action_du_jour_definir" in morning
    assert "raccroche" in " ".join(call_context("live", False, True))
    assert call_context(None, True, True) == []
    assert call_setting("call_early", "05:30") == "05:30"
    for key, bad in (("call_early", "5h30"), ("calls_enabled", "oui"), ("ntfy_topic", "court"), ("call_public_url", "http://x")):
        with pytest.raises(CallError):
            call_setting(key, bad)


def test_calls_api(client, monkeypatch):
    from atlas_server import calls as calls_module
    sent = []
    monkeypatch.setattr(calls_module, "send_ntfy", lambda payload, s: sent.append(payload))
    st = client.app.state.atlas
    st.calls.send = calls_module.send_ntfy
    assert client.get("/api/preferences").json()["ntfy_topic"].startswith("atlas-")  # sujet secret généré au démarrage
    assert client.post("/api/calls/test").json()["ok"] and sent[0]["click"].endswith("/appel?slot=test")
    assert client.patch("/api/preferences", json={"call_early": "6h"}).status_code == 400
    assert client.patch("/api/preferences", json={"call_early": "06:00", "call_afternoon_enabled": False}).status_code == 200
    info = client.post("/api/calls/goodnight").json()
    assert info["appel_du_matin"] in ("06:00", "08:00")
    status = client.get("/api/calls").json()
    assert status["coucher"] and [p["slot"] for p in status["aujourdhui"]] == ["morning", "evening"]
    assert client.post("/api/calls/answered", json={"slot": "pirate"}).status_code == 422
