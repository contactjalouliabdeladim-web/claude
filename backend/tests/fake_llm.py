"""Faux fournisseur de modèle compatible OpenAI, pour tester Atlas contre un VRAI Hermes
sans abonnement ni dépense. Comportement choisi d'après le dernier message utilisateur :

* contient « outil »     → appel de l'outil ``terminal`` (echo inoffensif), puis réponse
* contient « dangereux » → appel ``terminal`` avec une commande qui exige une approbation
* contient « quota »     → erreur 429 insufficient_quota
* contient « lent »      → réponse diffusée lentement (~20 s) pour tester l'arrêt
* sinon                  → réponse courte qui indique si la personnalité (SOUL.md) et le
                           contexte d'Atlas sont arrivés dans le prompt système.

Lancement : python fake_llm.py 9900
Chaque requête reçue est ajoutée à ``fake_llm_requests.jsonl`` (pour compter les appels).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
LOG = Path(__file__).with_name("fake_llm_requests.jsonl")


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def chunk(delta, finish=None):
    return {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": "fake-model", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "fake-model", "object": "model", "owned_by": "test"}]}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    system = " ".join(text_of(m.get("content")) for m in messages if m.get("role") == "system")
    last = messages[-1] if messages else {}
    last_user = next((text_of(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "last_role": last.get("role"), "last_user": last_user[:200],
                             "tools": len(body.get("tools") or []), "system_head": system[:160]}) + "\n")
    stream = bool(body.get("stream"))
    lowered = last_user.lower()
    tool_call = None
    text = None
    if last.get("role") == "tool":
        text = "C'est fait, l'action est terminée."
    elif "quota" in lowered:
        return JSONResponse({"error": {"message": "You exceeded your current quota, please check your plan.",
                                       "type": "insufficient_quota", "code": "insufficient_quota"}}, status_code=429)
    elif "dangereux" in lowered:
        tool_call = {"command": "rm -rf /tmp/atlas-test-dangerous-dir"}
    elif "outil" in lowered:
        tool_call = {"command": "echo atlas-test"}
    else:
        soul = "personnalité reçue" if "ATLAS-SOUL-MARKER" in system else "personnalité absente"
        ctx = "contexte reçu" if "Africa/Casablanca" in system else "contexte absent"
        text = f"Bonjour ! Réponse du faux modèle ({soul}, {ctx})."
        if "lent" in lowered:
            text = " ".join(["mot"] * 40)
    if tool_call is not None:
        call = {"index": 0, "id": f"call_{int(time.time()*1000)}", "type": "function",
                "function": {"name": "terminal", "arguments": json.dumps(tool_call)}}
        if not stream:
            return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": "fake-model",
                    "choices": [{"index": 0, "finish_reason": "tool_calls",
                                 "message": {"role": "assistant", "content": None,
                                             "tool_calls": [{k: v for k, v in call.items() if k != "index"}]}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

        async def gen_tool():
            yield f"data: {json.dumps(chunk({'role': 'assistant', 'tool_calls': [call]}))}\n\n"
            yield f"data: {json.dumps(chunk({}, 'tool_calls'))}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen_tool(), media_type="text/event-stream")

    if not stream:
        return {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": "fake-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}}
    slow = "lent" in lowered and last.get("role") != "tool"

    async def gen_text():
        yield f"data: {json.dumps(chunk({'role': 'assistant', 'content': ''}))}\n\n"
        for word in text.split(" "):
            yield f"data: {json.dumps(chunk({'content': word + ' '}))}\n\n"
            await asyncio.sleep(0.5 if slow else 0.02)
        final = chunk({}, "stop")
        final["usage"] = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen_text(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9900, log_level="warning")
