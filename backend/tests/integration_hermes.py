"""Test d'intégration de bout en bout : Atlas ↔ VRAI Hermes (serveur API) ↔ faux modèle.

Prérequis (voir docs/TESTS-ET-LIMITES.md) :
  * ``tests/fake_llm.py`` lancé sur le port 9900 ;
  * ``hermes gateway run`` avec un HERMES_HOME de test (provider custom → faux modèle,
    API_SERVER_ENABLED=true, API_SERVER_KEY=<clé>) et un SOUL.md contenant ATLAS-SOUL-MARKER.

Usage : python tests/integration_hermes.py <clé API Hermes>
Le script démarre lui-même Atlas (port 8766, dossier de données temporaire).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
FAKE_LOG = Path(__file__).with_name("fake_llm_requests.jsonl")
PORT = 8766
BASE = f"http://localhost:{PORT}"
ORIGIN = {"Origin": BASE}
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("  OK   " if ok else "  ÉCHEC ") + name + (f" — {detail}" if detail else ""), flush=True)


def llm_calls() -> int:
    return len(FAKE_LOG.read_text().splitlines()) if FAKE_LOG.exists() else 0


class AtlasProc:
    def __init__(self, data_dir: Path, hermes_key: str, hermes_url: str = "http://127.0.0.1:8642"):
        self.data_dir, self.key, self.url = data_dir, hermes_key, hermes_url
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = dict(os.environ, ATLAS_DATA_DIR=str(self.data_dir), ATLAS_PORT=str(PORT),
                   HERMES_API_KEY=self.key, HERMES_URL=self.url, ATLAS_DEMO="0", ATLAS_STT="off",
                   PYTHONPATH=str(ROOT))
        self.proc = subprocess.Popen([sys.executable, "-m", "atlas_server", "serve"], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                if httpx.get(f"{BASE}/api/public/status", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError("Atlas n'a pas démarré")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def code(self) -> str:
        import re
        return re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", (self.data_dir / "CODE-ACCES.txt").read_text()).group(0)


class Client:
    def __init__(self, code: str):
        self.http = httpx.AsyncClient(base_url=BASE, headers=ORIGIN, timeout=30)
        self.code = code
        self.events: list[dict] = []
        self.ws = None
        self.task = None

    async def login(self) -> None:
        r = await self.http.post("/api/auth/login", json={"code": self.code, "device_label": "test"})
        assert r.status_code == 200, r.text

    async def connect_ws(self) -> None:
        cookie = "; ".join(f"{k}={v}" for k, v in self.http.cookies.items())
        self.ws = await websockets.connect(f"ws://localhost:{PORT}/api/ws", origin=BASE,
                                           additional_headers={"Cookie": cookie})

        async def reader():
            async for raw in self.ws:
                self.events.append(json.loads(raw))
        self.task = asyncio.create_task(reader())

    async def wait_for(self, pred, timeout: float = 60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for ev in self.events:
                if pred(ev):
                    return ev
            await asyncio.sleep(0.1)
        raise TimeoutError("événement attendu non reçu")

    async def send(self, conv: str, text: str, req: str | None = None, device: str = "dev_test01"):
        req = req or "req_" + os.urandom(8).hex()
        r = await self.http.post(f"/api/conversations/{conv}/messages",
                                 json={"request_id": req, "content": text, "device_id": device})
        return req, r

    async def completed(self, msg_id: str, timeout: float = 90.0) -> dict:
        ev = await self.wait_for(lambda e: e.get("type") == "message.completed" and e["message"]["id"] == msg_id, timeout)
        return ev["message"]

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
        if self.task:
            self.task.cancel()
        await self.http.aclose()


async def scenario(hermes_key: str) -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="atlas-integ-"))
    atlas = AtlasProc(data_dir, hermes_key)
    atlas.start()
    c = Client(atlas.code())
    try:
        # --- sécurité --------------------------------------------------------------
        r = await c.http.get("/api/bootstrap")
        record("Accès refusé sans connexion", r.status_code == 401)
        r = await c.http.post("/api/auth/login", json={"code": "AAAA-BBBB-CCCC"})
        record("Mauvais code refusé", r.status_code == 401)
        await c.login()
        r = await c.http.post("/api/conversations", headers={"Origin": "http://evil.example"})
        record("Origine étrangère refusée", r.status_code == 403)
        r = await c.http.get("/api/public/status", headers={"Host": "evil.example"})
        record("Hôte inconnu refusé (anti-rebinding)", r.status_code == 421)
        try:
            ws = await websockets.connect(f"ws://localhost:{PORT}/api/ws", origin="http://evil.example")
            await ws.recv()
            record("WebSocket d'origine étrangère refusé", False)
        except Exception:
            record("WebSocket d'origine étrangère refusé", True)

        boot = (await c.http.get("/api/bootstrap")).json()
        record("Hermes détecté en mode sessions", boot["hermes"]["mode"] == "sessions", boot["hermes"]["mode"])
        blob = json.dumps(boot)
        record("Aucun secret dans les données envoyées au navigateur",
               hermes_key not in blob and "sk-fake" not in blob)
        await c.connect_ws()
        await c.wait_for(lambda e: e.get("type") == "hello")

        # --- réponse réelle, personnalité, contexte, flux ------------------------------------
        conv = (await c.http.post("/api/conversations")).json()["id"]
        before = llm_calls()
        req, r = await c.send(conv, "Bonjour Atlas")
        assistant_id = r.json()["assistant_message"]["id"]
        msg = await c.completed(assistant_id)
        record("Réponse réelle d'Hermes reçue", msg["status"] == "complete" and "faux modèle" in msg["content"], msg["content"][:80])
        record("Personnalité Hermes (SOUL.md) utilisée", "personnalité reçue" in msg["content"])
        record("Contexte date/fuseau transmis", "contexte reçu" in msg["content"])
        deltas = [e for e in c.events if e.get("type") == "message.delta" and e["message_id"] == assistant_id]
        record("Réponse affichée progressivement", len(deltas) > 3, f"{len(deltas)} fragments")
        record("Modèle réel rapporté par Hermes", msg["meta"].get("runtime", {}).get("model") == "fake-model")
        calls_turn = llm_calls() - before

        # --- doublon ---------------------------------------------------------------
        before = llm_calls()
        _, r2 = await c.send(conv, "Bonjour Atlas", req=req)
        await asyncio.sleep(2)
        record("Doublon (même request_id) non ré-exécuté", r2.json().get("duplicate") is True and llm_calls() == before)

        # --- continuité de session ------------------------------------------------------
        conv_row = next(x for x in (await c.http.get("/api/conversations")).json() if x["id"] == conv)
        sid = conv_row["hermes_session_id"]
        _, r = await c.send(conv, "Deuxième message")
        await c.completed(r.json()["assistant_message"]["id"])
        conv_row2 = next(x for x in (await c.http.get("/api/conversations")).json() if x["id"] == conv)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8642", headers={"Authorization": f"Bearer {hermes_key}"}) as h:
            hm = (await h.get(f"/api/sessions/{conv_row2['hermes_session_id']}/messages")).json()
        hermes_msgs = hm.get("messages", hm.get("data", []))
        record("Même session Hermes conservée", sid == conv_row2["hermes_session_id"], sid)
        record("Historique tenu par Hermes", len(hermes_msgs) >= 4, f"{len(hermes_msgs)} messages côté Hermes")

        # --- outils et activité ------------------------------------------------------------
        _, r = await c.send(conv, "Utilise un outil s'il te plaît")
        msg = await c.completed(r.json()["assistant_message"]["id"])
        acts = (await c.http.get("/api/activity")).json()
        labels = [a["label"] for a in acts]
        record("Outil réel visible dans l'activité", any("J'exécute une commande" in l for l in labels))
        states = [e.get("state") for e in c.events if e.get("type") == "atlas.state"]
        record("Orbe : états réfléchir → agir → repos observés", "thinking" in states and "acting" in states and states[-1] == "idle")

        # --- occupé -----------------------------------------------------------------------
        _, r_slow = await c.send(conv, "Réponds lentement (lent)")
        await asyncio.sleep(1.5)
        _, r_busy = await c.send(conv, "Pendant ce temps")
        record("Deuxième envoi refusé pendant une exécution (occupé)", r_busy.status_code == 409)
        # --- arrêt ------------------------------------------------------------------------
        await c.wait_for(lambda e: e.get("type") == "atlas.state" and e.get("run_id"))
        stop = (await c.http.post("/api/turn/stop")).json()
        msg = await c.completed(r_slow.json()["assistant_message"]["id"])
        record("Arrêt d'une tâche en cours", stop.get("ok") and msg["status"] == "cancelled", msg["status"])

        # --- approbation ---------------------------------------------------------------------
        target = Path("/tmp/atlas-test-dangerous-dir")
        target.mkdir(exist_ok=True)
        (target / "temoin.txt").write_text("ne pas supprimer")
        _, r = await c.send(conv, "Fais une action dangereux")
        ev = await c.wait_for(lambda e: e.get("type") == "atlas.state" and e.get("state") == "waiting_approval", 60)
        record("Demande d'approbation transmise à l'interface", bool(ev.get("approval")), (ev.get("approval") or {}).get("command", "")[:60])
        ans = await c.http.post("/api/turn/approval", json={"choice": "deny"})
        msg = await c.completed(r.json()["assistant_message"]["id"])
        record("Refus respecté (commande non exécutée)", ans.status_code == 200 and (target / "temoin.txt").exists())
        shutil.rmtree(target, ignore_errors=True)

        # --- quota ---------------------------------------------------------------------------
        _, r = await c.send(conv, "Simule quota")
        msg = await c.completed(r.json()["assistant_message"]["id"], timeout=180)
        record("Refus du fournisseur (quota) affiché clairement", msg["status"] == "error" and msg["meta"].get("error_kind") == "quota",
               f"{msg['status']} / {msg['meta'].get('error_kind')} / {(msg['meta'].get('error') or msg['content'])[:80]}")

        # --- repos : aucun appel au modèle ------------------------------------------------------
        before = llm_calls()
        await asyncio.sleep(35)
        record("Aucun appel au modèle au repos (35 s, WebSocket ouvert)", llm_calls() == before)

        # --- persistance ---------------------------------------------------------------------
        await c.http.post("/api/notes", json={"content": "Note de test persistante"})
        await c.http.post("/api/priorities", json={"title": "Priorité persistante"})
        await c.close()
        atlas.stop()
        atlas.start()
        c = Client(atlas.code())
        await c.login()
        notes = (await c.http.get("/api/notes")).json()
        prios = (await c.http.get("/api/priorities")).json()["items"]
        msgs = (await c.http.get(f"/api/conversations/{conv}/messages")).json()["messages"]
        record("Notes conservées après redémarrage", any(n["content"] == "Note de test persistante" for n in notes))
        record("Priorités conservées après redémarrage", any(p["title"] == "Priorité persistante" for p in prios))
        record("Conversation retrouvée après redémarrage", len(msgs) >= 8, f"{len(msgs)} messages")

        # --- redémarrage pendant un tour ----------------------------------------------------------
        await c.connect_ws()
        _, r = await c.send(conv, "Encore une réponse lente (lent)")
        mid = r.json()["assistant_message"]["id"]
        await asyncio.sleep(2)
        before = llm_calls()
        await c.close()
        atlas.stop()
        atlas.start()
        c = Client(atlas.code())
        await c.login()
        await asyncio.sleep(3)
        msgs = (await c.http.get(f"/api/conversations/{conv}/messages")).json()["messages"]
        m = next(x for x in msgs if x["id"] == mid)
        record("Tour coupé par un redémarrage marqué « interrompu », sans relance", m["status"] == "interrupted" and llm_calls() <= before + 1,
               m["status"])
        await c.close()
        atlas.stop()

        # --- Hermes indisponible ----------------------------------------------------------------
        bad = AtlasProc(data_dir, hermes_key, hermes_url="http://127.0.0.1:8699")
        bad.start()
        c = Client(bad.code())
        await c.login()
        boot = (await c.http.get("/api/bootstrap")).json()
        _, r = await c.send(conv, "Hermes est-il là ?")
        record("Hermes indisponible : état clair et envoi refusé proprement",
               not boot["hermes"]["reachable"] and r.status_code == 503, r.json().get("detail", {}).get("message", "")[:80])
        await c.close()
        bad.stop()

        # --- mauvaise clé -------------------------------------------------------------------------
        wrong = AtlasProc(data_dir, "0" * 64)
        wrong.start()
        c = Client(wrong.code())
        await c.login()
        boot = (await c.http.get("/api/bootstrap")).json()
        record("Clé refusée par Hermes détectée", boot["hermes"]["reachable"] and boot["hermes"]["error_kind"] == "auth")
        await c.close()
        wrong.stop()
        print(f"\nAppels au faux modèle pour un tour simple : {calls_turn}")
    finally:
        atlas.stop()
        shutil.rmtree(data_dir, ignore_errors=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    asyncio.run(scenario(sys.argv[1]))
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} vérifications réussies.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
