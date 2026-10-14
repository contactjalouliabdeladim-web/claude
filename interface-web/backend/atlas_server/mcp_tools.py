"""Outils d'Atlas exposés à Hermes par MCP (logique commune aux transports stdio et HTTP).

Chaque écriture passe par le vrai stockage, est diffusée à l'interface et inscrite dans
l'activité ; le résultat renvoyé à Hermes décrit l'état réellement enregistré.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from . import __version__
from .cockpit import CockpitError, cockpit_for
from .db import new_id
from .tasks import TaskError, public_task
from .util import today_iso

log = logging.getLogger("atlas.mcp")
KNOWN_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
PRIORITY_STATUS = {"a_faire": "todo", "en_cours": "doing", "terminee": "done"}

INSTRUCTIONS = (
    "Outils du tableau de bord Atlas de l'utilisateur (tâches, priorités du jour, notes rapides, finances, "
    "pages partagées). Cette base est commune à Atlas (Hermes) et à Claude (Claude Code) : ce que l'un "
    "écrit, l'autre le lit. Les pages servent de mémoire de travail partagée (projets, décisions, "
    "passations) : consulte-les quand un sujet a pu être traité par l'autre assistant. "
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
    {"name": "finance_resume", "description": "Consulte les encaissements, dépenses et soldes du tableau de bord business Atlas. Les devises restent séparées.",
     "inputSchema": {"type": "object", "properties": {
         "periode": {"type": "string", "enum": ["aujourdhui", "ce_mois", "tout"], "default": "aujourdhui"}}}},
    {"name": "finance_ajouter", "description": "Enregistre un encaissement réellement reçu ou une dépense réellement payée dans la base financière partagée Atlas. À utiliser uniquement quand l'utilisateur demande explicitement de noter ce mouvement.",
     "inputSchema": {"type": "object", "required": ["type", "montant", "devise"], "properties": {
         "type": {"type": "string", "enum": ["income", "expense"]},
         "montant": {"type": "number", "exclusiveMinimum": 0, "description": "Montant en unité principale, ex. 125.50"},
         "devise": {"type": "string", "enum": ["MAD", "EUR"]},
         "date": {"type": "string", "description": "AAAA-MM-JJ, par défaut aujourd'hui (Casablanca)"},
         "client": {"type": "string"}, "categorie": {"type": "string"}, "note": {"type": "string"}}}},
    {"name": "cockpit", "description": "Cockpit 120K : où en est l'utilisateur de son objectif de bénéfice net "
     "(cumul acquis, reste, %, rythme nécessaire et réel par semaine, avance/retard en jours et en DH), "
     "l'action du jour qui rapporte si elle est fixée, et les encaissements par client. Cite ces chiffres "
     "tels quels, sans les arrondir autrement.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "action_du_jour_definir", "description": "Fixe l'« action du jour qui rapporte » affichée en tête du "
     "tableau de bord : UNE seule action, la plus courte vers de l'argent encaissé (relancer un prospect, envoyer "
     "un devis, encaisser une facture…). Remplace celle du jour s'il y en avait une.",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string", "description": "Formulation courte et actionnable, ex. « Relancer Kick-Off TV pour la facture d'octobre »"},
         "pourquoi": {"type": "string", "description": "En une phrase : pourquoi c'est le chemin le plus court vers de l'argent"}}}},
    {"name": "pages_lister", "description": "Liste les pages partagées entre Atlas et Claude (titre, étiquettes, "
     "auteur et date de la dernière modification), les plus récentes d'abord.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "page_lire", "description": "Lit le contenu complet d'une page partagée (par titre ou par id).",
     "inputSchema": {"type": "object", "required": ["titre"], "properties": {
         "titre": {"type": "string", "description": "Titre exact (casse indifférente) ou id de la page"}}}},
    {"name": "page_ecrire", "description": "Crée une page partagée ou met à jour celle qui porte ce titre. "
     "Contenu en Markdown. mode « ajouter » complète la page existante au lieu de la remplacer "
     "(idéal pour un journal ou une passation).",
     "inputSchema": {"type": "object", "required": ["titre", "contenu"], "properties": {
         "titre": {"type": "string"}, "contenu": {"type": "string"},
         "mode": {"type": "string", "enum": ["remplacer", "ajouter"], "default": "remplacer"},
         "etiquettes": {"type": "string", "description": "Mots-clés séparés par des virgules, ex. « projet, scaleo »"}}}},
    {"name": "pages_chercher", "description": "Cherche un texte dans les titres, contenus et étiquettes des pages partagées.",
     "inputSchema": {"type": "object", "required": ["texte"], "properties": {"texte": {"type": "string"}}}},
    {"name": "page_supprimer", "description": "Supprime une page partagée, uniquement sur demande explicite.",
     "inputSchema": {"type": "object", "required": ["titre"], "properties": {"titre": {"type": "string"}}}},
]
MAX_PAGE_CHARS = 200_000
SCOPES = {"aujourdhui": "today", "a_venir": "upcoming", "en_retard": "overdue", "terminees": "done", "toutes": "all"}


class ToolError(Exception):
    pass


def public_page(page: dict, full: bool = True) -> dict:
    out = {"id": page["id"], "titre": page["title"], "etiquettes": page["tags"],
           "creee_par": page["created_by"], "modifiee_par": page["updated_by"],
           "modifiee_le": time.strftime("%Y-%m-%d %H:%M", time.localtime(page["updated_at"]))}
    if full:
        out["contenu"] = page["content"]
    return out


async def call_tool(st: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tz = st.turns.timezone()
    tasks = st.tasks
    agent = getattr(st, "agent", "atlas")
    who = agent.capitalize()
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
                                   notes=args.get("notes") or "", by=agent, default_today=True)
            return {"ok": True, "action": "ajoutée", "tache": public_task(task, tz)}
        if name == "tache_modifier":
            changes: dict[str, Any] = {"title": args.get("titre"), "notes": args.get("notes")}
            if "date" in args:
                changes["due_date"] = args.get("date")
            if "heure" in args:
                changes["due_time"] = args.get("heure")
            task = await tasks.update(str(args.get("id", "")), by=agent, **changes)
            return {"ok": True, "action": "modifiée", "tache": public_task(task, tz)}
        if name == "tache_terminer":
            done = args.get("terminee", True) is not False
            task = await tasks.update(str(args.get("id", "")), by=agent, status="done" if done else "todo")
            return {"ok": True, "action": "terminée" if done else "rouverte", "tache": public_task(task, tz)}
        if name == "tache_supprimer":
            task = await tasks.delete(str(args.get("id", "")), by=agent)
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
            await st.hub.broadcast({"type": "priorities.changed", "day": day, "by": agent})
            await st.turns.activity("taches", f"{who} a ajouté la priorité « {title[:60]} »", "ok")
            return {"ok": True, "action": "ajoutée", "priorite": {"id": pid, "titre": title, "statut": "a_faire"}}
        if name == "priorite_statut":
            status = PRIORITY_STATUS.get(str(args.get("statut")))
            item = st.db.get_priority(str(args.get("id", "")))
            if not status or not item:
                raise ToolError("Priorité ou statut introuvable.")
            st.db.execute("UPDATE priorities SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), item["id"]))
            await st.hub.broadcast({"type": "priorities.changed", "day": item["day"], "by": agent})
            await st.turns.activity("taches", f"{who} a changé le statut de « {item['title'][:60]} »", "ok",
                                    detail=args.get("statut"))
            return {"ok": True, "priorite": {"id": item["id"], "titre": item["title"], "statut": args.get("statut")}}
        if name == "note_ajouter":
            content = str(args.get("contenu") or "").strip()[:20000]
            if not content:
                raise ToolError("Note vide.")
            now = time.time()
            nid = new_id("note")
            st.db.execute("INSERT INTO notes (id, content, created_at, updated_at) VALUES (?,?,?,?)", (nid, content, now, now))
            await st.hub.broadcast({"type": "notes.changed", "origin": agent})
            await st.turns.activity("taches", f"{who} a ajouté une note", "ok", detail=content[:120])
            return {"ok": True, "note": {"id": nid, "contenu": content}}
        if name == "notes_chercher":
            text = str(args.get("texte") or "").strip().lower()
            found = [n for n in st.db.list_notes() if text and text in n["content"].lower()][:20]
            return {"nombre": len(found), "notes": [{"id": n["id"], "contenu": n["content"][:2000]} for n in found]}
        if name == "finance_resume":
            today = today_iso(tz)
            period = args.get("periode") or "aujourdhui"
            if period == "aujourdhui":
                start = end = today
            elif period == "ce_mois":
                start, end = today[:7] + "-01", today
            elif period == "tout":
                start = end = None
            else:
                raise ToolError("Période financière inconnue.")
            return {"periode": period, "date_reference": today,
                    "totaux": st.db.finance_totals(start=start, end=end),
                    "mouvements_recents": st.db.list_finance_transactions(start=start, end=end, limit=20)}
        if name == "finance_ajouter":
            kind = args.get("type")
            currency = args.get("devise")
            if kind not in ("income", "expense") or currency not in ("MAD", "EUR"):
                raise ToolError("Type de mouvement ou devise invalide.")
            try:
                amount = Decimal(str(args.get("montant")))
            except (InvalidOperation, ValueError):
                raise ToolError("Montant invalide.")
            if not amount.is_finite() or amount <= 0 or amount > Decimal("100000000000"):
                raise ToolError("Le montant doit être positif et rester dans la limite autorisée.")
            if amount != amount.quantize(Decimal("0.01")):
                raise ToolError("Le montant ne peut pas dépasser deux décimales.")
            raw_day = args.get("date") or today_iso(tz)
            try:
                day = date.fromisoformat(str(raw_day)).isoformat()
            except ValueError:
                raise ToolError("Date invalide : utilise AAAA-MM-JJ.")
            client = str(args.get("client") or "").strip()[:100]
            category = str(args.get("categorie") or "").strip()[:80]
            note = str(args.get("note") or "").strip()[:500]
            row = st.db.add_finance_transaction(kind=kind, amount_cents=int(amount * 100), currency=currency,
                                                date=day, client=client, category=category, note=note)
            await st.hub.broadcast({"type": "finance.changed"})
            await st.turns.activity("finances", f"Mouvement financier ajouté ({kind}, {currency})", "ok",
                                    detail=f"{amount} {currency}" + (f" · {client}" if client else ""))
            return {"ok": True, "transaction": row}
        if name == "cockpit":
            today = today_iso(tz)
            data = cockpit_for(st.db, st.db.get_preferences(), today)
            data["encaissements_par_client"] = st.db.finance_by_client()
            return data
        if name == "action_du_jour_definir":
            text = " ".join(str(args.get("action") or "").split())[:200]
            if not text:
                raise ToolError("Action vide.")
            action = {"jour": today_iso(tz), "action": text, "pourquoi": " ".join(str(args.get("pourquoi") or "").split())[:300],
                      "par": agent, "le": time.time()}
            st.db.set_preference("money_action", action)
            await st.hub.broadcast({"type": "cockpit.changed", "by": agent})
            await st.turns.activity("taches", f"{who} a fixé l'action du jour qui rapporte", "ok", detail=text)
            return {"ok": True, "action_du_jour": action}
        if name == "pages_lister":
            pages = st.db.list_pages()
            return {"nombre": len(pages), "pages": [public_page(p, full=False) for p in pages]}
        if name == "page_lire":
            page = st.db.get_page(str(args.get("titre") or "").strip())
            if not page:
                raise ToolError("Page introuvable : vérifie le titre avec pages_lister.")
            return {"page": public_page(page)}
        if name == "page_ecrire":
            title = " ".join(str(args.get("titre") or "").split())[:200]
            content = str(args.get("contenu") or "").strip()
            if not title or not content:
                raise ToolError("Titre ou contenu vide.")
            if len(content) > MAX_PAGE_CHARS:
                raise ToolError(f"Contenu trop long ({MAX_PAGE_CHARS} caractères maximum).")
            tags = args.get("etiquettes")
            tags = ", ".join(t.strip() for t in str(tags).split(",") if t.strip())[:300] if tags is not None else None
            page, created = st.db.save_page(title, content, tags=tags, by=agent, append=args.get("mode") == "ajouter")
            await st.hub.broadcast({"type": "pages.changed", "id": page["id"], "by": agent})
            verb = "créé" if created else "mis à jour"
            await st.turns.activity("pages", f"{who} a {verb} la page « {title[:60]} »", "ok")
            return {"ok": True, "action": "créée" if created else "mise à jour", "page": public_page(page, full=False)}
        if name == "pages_chercher":
            text = str(args.get("texte") or "").strip()
            if not text:
                raise ToolError("Texte de recherche vide.")
            found = st.db.search_pages(text)
            return {"nombre": len(found), "pages": [{**public_page(p, full=False), "extrait": p["content"][:500]}
                                                    for p in found]}
        if name == "page_supprimer":
            page = st.db.get_page(str(args.get("titre") or "").strip())
            if not page:
                raise ToolError("Page introuvable.")
            st.db.delete_page(page["id"])
            await st.hub.broadcast({"type": "pages.changed", "id": page["id"], "by": agent})
            await st.turns.activity("pages", f"{who} a supprimé la page « {page['title'][:60]} »", "ok")
            return {"ok": True, "action": "supprimée", "page": public_page(page, full=False)}
    except (TaskError, CockpitError) as exc:
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
