"""Outils d'Atlas exposés à Hermes par MCP (logique commune aux transports stdio et HTTP).

Chaque écriture passe par le vrai stockage, est diffusée à l'interface et inscrite dans
l'activité ; le résultat renvoyé à Hermes décrit l'état réellement enregistré.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import __version__
from .db import new_id
from .tasks import TaskError, public_task
from .util import today_iso

log = logging.getLogger("atlas.mcp")
KNOWN_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
PRIORITY_STATUS = {"a_faire": "todo", "en_cours": "doing", "terminee": "done"}

INSTRUCTIONS = (
    "Outils du tableau de bord Atlas de l'utilisateur (tâches, priorités du jour, notes rapides). "
    "Tout ce que tu y écris apparaît immédiatement dans son interface. Utilise-les quand il te demande "
    "explicitement d'ajouter, modifier, terminer ou consulter une tâche, une priorité ou une note. "
    "Ne crée rien qu'il n'a pas demandé et ne marque rien comme terminé sans qu'il le dise. "
    "Après une écriture, confirme brièvement à partir du résultat renvoyé."
)

TOOLS: list[dict[str, Any]] = [
    {"name": "journee", "description": "Résumé de la journée de l'utilisateur dans Atlas : date, tâches à faire "
     "aujourd'hui (et en retard), nombre de tâches terminées aujourd'hui, priorités du jour.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "taches_lister", "description": "Liste les tâches du tableau de bord Atlas.",
     "inputSchema": {"type": "object", "properties": {
         "periode": {"type": "string", "enum": ["aujourdhui", "a_venir", "en_retard", "terminees", "toutes"],
                     "description": "aujourdhui = à faire aujourd'hui, en retard ou sans date (par défaut)"}}}},
    {"name": "tache_ajouter", "description": "Ajoute une tâche dans l'interface Atlas de l'utilisateur. À utiliser "
     "quand il demande d'ajouter, noter ou planifier une tâche, une chose à faire ou un rappel.",
     "inputSchema": {"type": "object", "required": ["titre"], "properties": {
         "titre": {"type": "string", "description": "Formulation courte et actionnable, ex. « Appeler le comptable »"},
         "date": {"type": "string", "description": "AAAA-MM-JJ, « aujourd'hui » ou « demain ». Par défaut : aujourd'hui."},
         "heure": {"type": "string", "description": "HH:MM si une heure est précisée"},
         "notes": {"type": "string", "description": "Détails facultatifs"}}}},
    {"name": "tache_modifier", "description": "Modifie une tâche existante (titre, date, heure, notes). "
     "Obtenir l'id avec taches_lister.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": {"type": "string"}, "titre": {"type": "string"},
         "date": {"type": "string", "description": "AAAA-MM-JJ, « aujourd'hui », « demain », ou vide pour retirer la date"},
         "heure": {"type": "string", "description": "HH:MM, ou vide pour retirer l'heure"},
         "notes": {"type": "string"}}}},
    {"name": "tache_terminer", "description": "Marque une tâche comme terminée (ou la rouvre avec terminee=false). "
     "Seulement si l'utilisateur dit l'avoir faite.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {
         "id": {"type": "string"}, "terminee": {"type": "boolean", "default": True}}}},
    {"name": "tache_supprimer", "description": "Supprime une tâche, uniquement sur demande explicite.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
    {"name": "priorite_ajouter", "description": "Ajoute une des 3 priorités du jour (maximum 3 par jour).",
     "inputSchema": {"type": "object", "required": ["titre"], "properties": {
         "titre": {"type": "string"}, "description": {"type": "string"}}}},
    {"name": "priorite_statut", "description": "Change le statut d'une priorité du jour.",
     "inputSchema": {"type": "object", "required": ["id", "statut"], "properties": {
         "id": {"type": "string"}, "statut": {"type": "string", "enum": list(PRIORITY_STATUS)}}}},
    {"name": "note_ajouter", "description": "Ajoute une note rapide dans l'interface Atlas (pas dans ta mémoire).",
     "inputSchema": {"type": "object", "required": ["contenu"], "properties": {"contenu": {"type": "string"}}}},
    {"name": "notes_chercher", "description": "Cherche dans les notes rapides de l'utilisateur.",
     "inputSchema": {"type": "object", "required": ["texte"], "properties": {"texte": {"type": "string"}}}},
]
SCOPES = {"aujourdhui": "today", "a_venir": "upcoming", "en_retard": "overdue", "terminees": "done", "toutes": "all"}


class ToolError(Exception):
    pass


async def call_tool(st: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tz = st.turns.timezone()
    tasks = st.tasks
    try:
        if name == "journee":
            return tasks.day_summary()
        if name == "taches_lister":
            scope = SCOPES.get(args.get("periode") or "aujourdhui")
            if not scope:
                raise ToolError("Période inconnue.")
            items = st.db.list_tasks(scope, today_iso(tz), limit=100)
            return {"periode": args.get("periode") or "aujourdhui", "nombre": len(items),
                    "taches": [public_task(t, tz) for t in items]}
        if name == "tache_ajouter":
            task = await tasks.add(args.get("titre"), due_date=args.get("date"), due_time=args.get("heure"),
                                   notes=args.get("notes") or "", by="atlas", default_today=True)
            return {"ok": True, "action": "ajoutée", "tache": public_task(task, tz)}
        if name == "tache_modifier":
            changes: dict[str, Any] = {"title": args.get("titre"), "notes": args.get("notes")}
            if "date" in args:
                changes["due_date"] = args.get("date")
            if "heure" in args:
                changes["due_time"] = args.get("heure")
            task = await tasks.update(str(args.get("id", "")), by="atlas", **changes)
            return {"ok": True, "action": "modifiée", "tache": public_task(task, tz)}
        if name == "tache_terminer":
            done = args.get("terminee", True) is not False
            task = await tasks.update(str(args.get("id", "")), by="atlas", status="done" if done else "todo")
            return {"ok": True, "action": "terminée" if done else "rouverte", "tache": public_task(task, tz)}
        if name == "tache_supprimer":
            task = await tasks.delete(str(args.get("id", "")), by="atlas")
            return {"ok": True, "action": "supprimée", "tache": public_task(task, tz)}
        if name == "priorite_ajouter":
            title = " ".join(str(args.get("titre") or "").split())[:200]
            if not title:
                raise ToolError("Titre vide.")
            day = today_iso(tz)
            with st.db.transaction() as db:
                count = len(db.all("SELECT id FROM priorities WHERE day = ?", (day,)))
                if count >= 3:
                    raise ToolError("Il y a déjà 3 priorités aujourd'hui : demande laquelle remplacer.")
                now = time.time()
                pid = new_id("prio")
                db.execute("INSERT INTO priorities (id, title, description, status, day, due, position, created_at, "
                           "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           (pid, title, str(args.get("description") or "")[:2000], "todo", day, None, count, now, now))
            await st.hub.broadcast({"type": "priorities.changed", "day": day, "by": "atlas"})
            await st.turns.activity("taches", f"Atlas a ajouté la priorité « {title[:60]} »", "ok")
            return {"ok": True, "action": "ajoutée", "priorite": {"id": pid, "titre": title, "statut": "a_faire"}}
        if name == "priorite_statut":
            status = PRIORITY_STATUS.get(str(args.get("statut")))
            item = st.db.get_priority(str(args.get("id", "")))
            if not status or not item:
                raise ToolError("Priorité ou statut introuvable.")
            st.db.execute("UPDATE priorities SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), item["id"]))
            await st.hub.broadcast({"type": "priorities.changed", "day": item["day"], "by": "atlas"})
            await st.turns.activity("taches", f"Atlas a changé le statut de « {item['title'][:60]} »", "ok",
                                    detail=args.get("statut"))
            return {"ok": True, "priorite": {"id": item["id"], "titre": item["title"], "statut": args.get("statut")}}
        if name == "note_ajouter":
            content = str(args.get("contenu") or "").strip()[:20000]
            if not content:
                raise ToolError("Note vide.")
            now = time.time()
            nid = new_id("note")
            st.db.execute("INSERT INTO notes (id, content, created_at, updated_at) VALUES (?,?,?,?)", (nid, content, now, now))
            await st.hub.broadcast({"type": "notes.changed", "origin": "atlas"})
            await st.turns.activity("taches", "Atlas a ajouté une note", "ok", detail=content[:120])
            return {"ok": True, "note": {"id": nid, "contenu": content}}
        if name == "notes_chercher":
            text = str(args.get("texte") or "").strip().lower()
            found = [n for n in st.db.list_notes() if text and text in n["content"].lower()][:20]
            return {"nombre": len(found), "notes": [{"id": n["id"], "contenu": n["content"][:2000]} for n in found]}
    except TaskError as exc:
        raise ToolError(str(exc)) from exc
    raise ToolError(f"Outil inconnu : {name}")




def _negotiate(requested: Any) -> str:
    return requested if requested in KNOWN_VERSIONS else "2025-06-18"


async def handle_message(st: Any, msg: dict) -> dict | None:
    """Traite un message JSON-RPC MCP. ``None`` pour une notification (pas de réponse)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        st.mark_mcp_seen()
        params = msg.get("params") or {}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": _negotiate(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "atlas", "version": __version__},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        st.mark_mcp_seen()
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        st.mark_mcp_seen()
        try:
            if not isinstance(args, dict):
                raise ToolError("Arguments invalides.")
            result = await call_tool(st, str(name), args)
            text, is_error = json.dumps(result, ensure_ascii=False), False
        except ToolError as exc:
            result, text, is_error = {"ok": False, "erreur": str(exc)}, f"Erreur : {exc}", True
        except Exception:
            log.exception("Outil MCP %s", name)
            result, text, is_error = {"ok": False, "erreur": "erreur interne"}, "Erreur interne d'Atlas.", True
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}], "structuredContent": result, "isError": is_error}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Méthode inconnue : {method}"}}
