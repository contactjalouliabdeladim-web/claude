"""Tâches du tableau de bord : règles communes à l'interface (REST) et aux outils d'Atlas (MCP).

Chaque modification est écrite en base, puis diffusée à toutes les fenêtres ouvertes et
inscrite dans le journal d'activité. La réponse renvoyée (à l'interface ou à Hermes) décrit
l'état réellement enregistré : c'est la « confirmation structurée ».
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from .util import now_in, today_iso

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
STATUS_LABEL = {"todo": "à faire", "done": "terminée"}


class TaskError(ValueError):
    pass


def parse_due_date(value: Any, tz: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace("’", "'")
    today = now_in(tz).date()
    keywords = {"aujourd'hui": 0, "aujourdhui": 0, "today": 0, "demain": 1, "tomorrow": 1, "après-demain": 2,
                "apres-demain": 2}
    if text in keywords:
        return (today + timedelta(days=keywords[text])).isoformat()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise TaskError("Date invalide : utiliser AAAA-MM-JJ, « aujourd'hui » ou « demain ».") from exc


def parse_due_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace("h", ":")
    if re.fullmatch(r"\d{1,2}:?", text):
        text = text.rstrip(":") + ":00"
    if re.fullmatch(r"\d:\d\d", text):
        text = "0" + text
    if not TIME_RE.match(text):
        raise TaskError("Heure invalide : utiliser HH:MM (ex. 14:30).")
    return text


def clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise TaskError("Le titre de la tâche est vide.")
    if len(title) > 200:
        raise TaskError("Titre trop long (200 caractères maximum).")
    return title


def describe_due(task: dict, tz: str) -> str:
    if not task.get("due_date"):
        return "sans date"
    today = now_in(tz).date()
    d = date.fromisoformat(task["due_date"])
    label = {0: "aujourd'hui", 1: "demain", -1: "hier"}.get((d - today).days, d.strftime("%d/%m/%Y"))
    return label + (f" à {task['due_time']}" if task.get("due_time") else "")


def public_task(task: dict, tz: str) -> dict:
    """Représentation renvoyée à Hermes : champs utiles + échéance lisible."""
    return {"id": task["id"], "titre": task["title"], "notes": task["notes"], "date": task["due_date"],
            "heure": task["due_time"], "statut": STATUS_LABEL.get(task["status"], task["status"]),
            "echeance": describe_due(task, tz), "ajoutee_par": "Atlas" if task["source"] == "atlas" else "toi"}


class TaskService:
    def __init__(self, db: Any, hub: Any, activity: Any, timezone: Any):
        self.db, self.hub, self._activity, self._tz = db, hub, activity, timezone

    @property
    def tz(self) -> str:
        return self._tz()

    async def _changed(self, action: str, task: dict, by: str) -> None:
        await self.hub.broadcast({"type": "tasks.changed", "action": action, "task": task, "by": by})
        verb = {"added": "ajouté", "updated": "modifié", "completed": "terminé", "reopened": "rouvert",
                "deleted": "supprimé"}[action]
        who = "Atlas a" if by == "atlas" else "Tu as"
        await self._activity("taches", f"{who} {verb} la tâche « {task['title'][:60]} »", "ok",
                             detail=describe_due(task, self.tz))

    async def add(self, title: Any, *, due_date: Any = None, due_time: Any = None, notes: Any = "",
                  by: str = "user", default_today: bool = False) -> dict:
        title = clean_title(title)
        notes = str(notes or "")[:2000]
        parsed_date = parse_due_date(due_date, self.tz)
        if parsed_date is None and default_today:
            parsed_date = today_iso(self.tz)
        task = self.db.add_task(title, notes=notes, due_date=parsed_date, due_time=parse_due_time(due_time),
                                source="atlas" if by == "atlas" else "user")
        await self._changed("added", task, by)
        return task

    async def update(self, tid: str, *, by: str = "user", **changes: Any) -> dict:
        task = self.db.get_task(tid)
        if not task:
            raise TaskError(f"Tâche introuvable : {tid}")
        fields: dict[str, Any] = {}
        if changes.get("title") is not None:
            fields["title"] = clean_title(changes["title"])
        if changes.get("notes") is not None:
            fields["notes"] = str(changes["notes"])[:2000]
        if "due_date" in changes:
            fields["due_date"] = parse_due_date(changes["due_date"], self.tz)
        if "due_time" in changes:
            fields["due_time"] = parse_due_time(changes["due_time"])
        if changes.get("status") is not None:
            if changes["status"] not in ("todo", "done"):
                raise TaskError("Statut invalide (todo ou done).")
            fields["status"] = changes["status"]
        updated = self.db.update_task(tid, **fields)
        action = "updated"
        if fields.get("status") == "done" and task["status"] != "done":
            action = "completed"
        elif fields.get("status") == "todo" and task["status"] == "done":
            action = "reopened"
        await self._changed(action, updated, by)
        return updated

    async def delete(self, tid: str, *, by: str = "user") -> dict:
        task = self.db.get_task(tid)
        if not task:
            raise TaskError(f"Tâche introuvable : {tid}")
        self.db.delete_task(tid)
        await self._changed("deleted", task, by)
        return task

    def day_summary(self) -> dict:
        tz = self.tz
        today = today_iso(tz)
        start = datetime.combine(now_in(tz).date(), datetime.min.time(), tzinfo=now_in(tz).tzinfo).timestamp()
        return {"date": today, "fuseau": tz,
                "taches_du_jour": [public_task(t, tz) for t in self.db.list_tasks("today", today)],
                "terminees_aujourdhui": self.db.done_today(start),
                "priorites": [{"id": p["id"], "titre": p["title"], "statut": p["status"]}
                              for p in self.db.list_priorities(today)]}
