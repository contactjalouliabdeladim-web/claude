"""Adaptateur de DÉMONSTRATION — réponses simulées, jamais présentées comme venant d'Hermes.

Activé uniquement avec ``ATLAS_DEMO=1``. Utilise une base séparée
(``atlas-demo.db``) et l'interface affiche un bandeau « Mode démonstration ».
Sert au développement de l'interface sans Hermes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from .hermes_adapter import HermesStatus, _maybe_await


class DemoAdapter:
    def __init__(self) -> None:
        self.status = HermesStatus(reachable=True, authenticated=True, mode="demo", model_alias="démo",
                                   features={"run_stop": True, "session_chat_streaming": True})
        self._stops: set[str] = set()
        self._counter = 0

    async def close(self) -> None:
        return None

    async def probe(self) -> HermesStatus:
        self.status.checked_at = time.time()
        return self.status

    async def list_skills(self) -> list[dict[str, Any]]:
        return [{"name": "suivi-quotidien", "description": "[DÉMO] Compétence fictive", "category": "demo"}]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return None

    async def create_session(self, title: str | None = None) -> str:
        return f"demo_{int(time.time())}"

    async def stream_turn(self, session_id: str, message: str, *, system_message: str | None = None,
                          on_event: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        self._counter += 1
        run_id = f"demo_run_{self._counter}"
        await _maybe_await(on_event({"type": "run_started", "run_id": run_id}))
        await asyncio.sleep(0.8)
        if "outil" in message.lower() or "note" in message.lower():
            await _maybe_await(on_event({"type": "tool_started", "tool": "demo_tool",
                                         "preview": "[DÉMO] action simulée"}))
            await asyncio.sleep(1.2)
            await _maybe_await(on_event({"type": "tool_completed", "tool": "demo_tool", "failed": False,
                                         "preview": "[DÉMO] résultat simulé"}))
        text = ("**[Mode démonstration]** Cette réponse est simulée : Hermes n'a pas été contacté.\n\n"
                f"Tu as écrit : « {message[:200]} ».\n\n- Le texte arrive progressivement\n"
                "- Les actions du panneau d'activité sont marquées DÉMO\n\n`ATLAS_DEMO=0` pour revenir au mode réel.")
        for word in text.split(" "):
            if run_id in self._stops:
                return {"type": "completed", "status": "cancelled", "content": "", "session_id": session_id,
                        "run_id": run_id, "usage": {}, "runtime": {}}
            await _maybe_await(on_event({"type": "delta", "text": word + " "}))
            await asyncio.sleep(0.04)
        return {"type": "completed", "status": "completed", "content": text, "session_id": session_id,
                "run_id": run_id, "usage": {}, "runtime": {"provider": "démo", "model": "démo"}}

    async def stop_run(self, run_id: str) -> bool:
        self._stops.add(run_id)
        return True

    async def answer_approval(self, run_id: str, choice: str, request_id: str | None = None) -> dict[str, Any]:
        return {"choice": choice}
