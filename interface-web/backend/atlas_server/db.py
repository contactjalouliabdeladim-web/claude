"""Stockage SQLite des données propres au tableau de bord.

Les données d'Hermes (mémoire, personnalité, sessions) restent chez Hermes.
Ici : conversations (référence à la session Hermes), messages affichés,
priorités, notes, préférences, journal d'activité et sessions de connexion.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

MAX_EVENTS = 3000

MIGRATIONS: list[str] = [
    # 1 — schéma initial
    """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        hermes_session_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL,
        request_id TEXT,
        run_id TEXT,
        origin_device TEXT,
        meta TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX idx_messages_conv ON messages(conversation_id, created_at);
    CREATE TABLE requests (
        request_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        user_message_id TEXT,
        assistant_message_id TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE priorities (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'todo',
        day TEXT NOT NULL,
        due TEXT,
        position INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX idx_priorities_day ON priorities(day, position);
    CREATE TABLE notes (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE preferences (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        category TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL,
        duration_ms INTEGER,
        detail TEXT,
        run_id TEXT,
        conversation_id TEXT
    );
    CREATE TABLE auth_sessions (
        token_hash TEXT PRIMARY KEY,
        device_label TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE TABLE secrets (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 2 — tâches (ajoutées par toi ou par Atlas via ses outils)
    """
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        due_date TEXT,
        due_time TEXT,
        status TEXT NOT NULL DEFAULT 'todo',
        source TEXT NOT NULL DEFAULT 'user',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        completed_at REAL
    );
    CREATE INDEX idx_tasks_due ON tasks(status, due_date);
    """,
    # 3 — journal des mouvements financiers (montants en centimes/unités mineures)
    """
    CREATE TABLE finance_transactions (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('income','expense')),
        amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
        currency TEXT NOT NULL CHECK(currency IN ('MAD','EUR')),
        date TEXT NOT NULL,
        client TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    CREATE INDEX idx_finance_date ON finance_transactions(date DESC, created_at DESC);
    """,
    # 4 — pages partagées : base de connaissances commune à Atlas (Hermes), Claude et l'utilisateur
    """
    CREATE TABLE pages (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL UNIQUE COLLATE NOCASE,
        content TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX idx_pages_updated ON pages(updated_at DESC);
    """,
]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # -- infrastructure -------------------------------------------------
    def _migrate(self) -> None:
        # BEGIN IMMEDIATE + relecture de la version : sûr si deux processus (Atlas et le serveur
        # d'outils lancé par Hermes) ouvrent la base en même temps.
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            while True:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    row = self._conn.execute("SELECT version FROM schema_version").fetchone()
                    current = row[0] if row else 0
                    if current >= len(MIGRATIONS):
                        self._conn.execute("COMMIT")
                        return
                    for statement in [s for s in MIGRATIONS[current].split(";") if s.strip()]:
                        self._conn.execute(statement)
                    self._conn.execute("DELETE FROM schema_version")
                    self._conn.execute("INSERT INTO schema_version (version) VALUES (?)", (current + 1,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    @property
    def schema_version(self) -> int:
        return self._conn.execute("SELECT version FROM schema_version").fetchone()[0]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def transaction(self):
        db = self

        class _Tx:
            def __enter__(self_inner):
                db._lock.acquire()
                db._conn.execute("BEGIN IMMEDIATE")
                return db

            def __exit__(self_inner, exc_type, exc, tb):
                try:
                    db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    db._lock.release()
                return False

        return _Tx()

    def backup_to(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- secrets / préférences ---------------------------------------------
    def get_secret(self, key: str) -> str | None:
        row = self.one("SELECT value FROM secrets WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_secret(self, key: str, value: str) -> None:
        self.execute("INSERT INTO secrets (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def get_preferences(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.all("SELECT key, value FROM preferences")}

    def set_preference(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO preferences (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))

    # -- conversations -------------------------------------------------------
    def create_conversation(self, title: str = "Nouvelle conversation") -> dict:
        now = time.time()
        conv = {"id": new_id("conv"), "title": title, "hermes_session_id": None,
                "created_at": now, "updated_at": now}
        self.execute("INSERT INTO conversations (id, title, hermes_session_id, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?)", (conv["id"], conv["title"], None, now, now))
        return conv

    def get_conversation(self, conv_id: str) -> dict | None:
        return self.one("SELECT * FROM conversations WHERE id = ?", (conv_id,))

    def list_conversations(self, limit: int = 100) -> list[dict]:
        return self.all(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c ORDER BY updated_at DESC LIMIT ?", (limit,))

    def update_conversation(self, conv_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE conversations SET {cols} WHERE id = ?", (*fields.values(), conv_id))

    def delete_conversation(self, conv_id: str) -> None:
        self.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self.execute("DELETE FROM requests WHERE conversation_id = ?", (conv_id,))

    # -- messages -----------------------------------------------------------
    def add_message(self, conversation_id: str, role: str, content: str, status: str = "complete",
                    request_id: str | None = None, origin_device: str | None = None,
                    meta: dict | None = None) -> dict:
        now = time.time()
        msg = {"id": new_id("msg"), "conversation_id": conversation_id, "role": role, "content": content,
               "status": status, "request_id": request_id, "run_id": None, "origin_device": origin_device,
               "meta": meta or {}, "created_at": now, "updated_at": now}
        self.execute(
            "INSERT INTO messages (id, conversation_id, role, content, status, request_id, run_id, "
            "origin_device, meta, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (msg["id"], conversation_id, role, content, status, request_id, None, origin_device,
             json.dumps(msg["meta"]), now, now))
        self.update_conversation(conversation_id)
        return msg

    def update_message(self, msg_id: str, **fields: Any) -> None:
        if "meta" in fields:
            fields["meta"] = json.dumps(fields["meta"])
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE messages SET {cols} WHERE id = ?", (*fields.values(), msg_id))

    def get_message(self, msg_id: str) -> dict | None:
        row = self.one("SELECT * FROM messages WHERE id = ?", (msg_id,))
        if row:
            row["meta"] = json.loads(row["meta"] or "{}")
        return row

    def list_messages(self, conversation_id: str) -> list[dict]:
        rows = self.all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
                        (conversation_id,))
        for row in rows:
            row["meta"] = json.loads(row["meta"] or "{}")
        return rows

    def mark_interrupted_messages(self) -> int:
        """Au démarrage : un message resté « en cours » appartient à une exécution perdue."""
        cur = self.execute(
            "UPDATE messages SET status = 'interrupted', updated_at = ? WHERE status = 'streaming'",
            (time.time(),))
        return cur.rowcount

    # -- requêtes (anti-doublon) ---------------------------------------------
    def get_request(self, request_id: str) -> dict | None:
        return self.one("SELECT * FROM requests WHERE request_id = ?", (request_id,))

    def add_request(self, request_id: str, conversation_id: str, user_message_id: str,
                    assistant_message_id: str) -> None:
        self.execute("INSERT INTO requests (request_id, conversation_id, user_message_id, "
                     "assistant_message_id, created_at) VALUES (?,?,?,?,?)",
                     (request_id, conversation_id, user_message_id, assistant_message_id, time.time()))

    # -- priorités ----------------------------------------------------------
    def list_priorities(self, day: str | None = None) -> list[dict]:
        if day:
            return self.all("SELECT * FROM priorities WHERE day = ? ORDER BY position, created_at", (day,))
        return self.all("SELECT * FROM priorities ORDER BY day DESC, position")

    def get_priority(self, pid: str) -> dict | None:
        return self.one("SELECT * FROM priorities WHERE id = ?", (pid,))

    # -- tâches ----------------------------------------------------------------
    TASK_FIELDS = ("title", "notes", "due_date", "due_time", "status")

    def get_task(self, tid: str) -> dict | None:
        return self.one("SELECT * FROM tasks WHERE id = ?", (tid,))

    def add_task(self, title: str, *, notes: str = "", due_date: str | None = None, due_time: str | None = None,
                 source: str = "user") -> dict:
        now = time.time()
        task = {"id": new_id("task"), "title": title, "notes": notes, "due_date": due_date, "due_time": due_time,
                "status": "todo", "source": source, "created_at": now, "updated_at": now, "completed_at": None}
        self.execute("INSERT INTO tasks (id, title, notes, due_date, due_time, status, source, created_at, updated_at, "
                     "completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(task.values()))
        return task

    def update_task(self, tid: str, **fields: Any) -> dict | None:
        fields = {k: v for k, v in fields.items() if k in self.TASK_FIELDS}
        if "status" in fields:
            fields["completed_at"] = time.time() if fields["status"] == "done" else None
        if fields:
            fields["updated_at"] = time.time()
            cols = ", ".join(f"{k} = ?" for k in fields)
            self.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), tid))
        return self.get_task(tid)

    def delete_task(self, tid: str) -> bool:
        return self.execute("DELETE FROM tasks WHERE id = ?", (tid,)).rowcount > 0

    def list_tasks(self, scope: str, today: str, limit: int = 200) -> list[dict]:
        """scope : today (aujourd'hui + en retard + sans date), upcoming, done, overdue, all."""
        order = "ORDER BY due_date IS NULL, due_date, due_time IS NULL, due_time, created_at"
        if scope == "today":
            sql = f"SELECT * FROM tasks WHERE status = 'todo' AND (due_date IS NULL OR due_date <= ?) {order}"
            params: tuple = (today,)
        elif scope == "upcoming":
            sql, params = f"SELECT * FROM tasks WHERE status = 'todo' AND due_date > ? {order}", (today,)
        elif scope == "overdue":
            sql, params = f"SELECT * FROM tasks WHERE status = 'todo' AND due_date < ? {order}", (today,)
        elif scope == "done":
            sql, params = "SELECT * FROM tasks WHERE status = 'done' ORDER BY completed_at DESC", ()
        else:
            sql, params = f"SELECT * FROM tasks {order}", ()
        return self.all(sql + " LIMIT ?", (*params, limit))

    def done_today(self, day_start: float) -> int:
        return self.one("SELECT COUNT(*) AS n FROM tasks WHERE status = 'done' AND completed_at >= ?", (day_start,))["n"]

    # -- notes ---------------------------------------------------------------
    def list_notes(self) -> list[dict]:
        return self.all("SELECT * FROM notes ORDER BY updated_at DESC")

    def get_note(self, nid: str) -> dict | None:
        return self.one("SELECT * FROM notes WHERE id = ?", (nid,))

    # -- pages partagées -------------------------------------------------------------
    def get_page(self, key: str) -> dict | None:
        """Par id ou par titre (sans tenir compte de la casse)."""
        return self.one("SELECT * FROM pages WHERE id = ? OR title = ?", (key, key))

    def list_pages(self, limit: int = 200) -> list[dict]:
        return self.all("SELECT id, title, tags, created_by, updated_by, created_at, updated_at, "
                        "length(content) AS size FROM pages ORDER BY updated_at DESC LIMIT ?", (limit,))

    def search_pages(self, text: str, limit: int = 20) -> list[dict]:
        like = f"%{text}%"
        return self.all("SELECT * FROM pages WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? "
                        "ORDER BY updated_at DESC LIMIT ?", (like, like, like, limit))

    def save_page(self, title: str, content: str, *, tags: str | None, by: str, append: bool = False) -> tuple[dict, bool]:
        """Crée la page ou la met à jour. Renvoie (page, créée)."""
        now = time.time()
        with self.transaction():
            page = self.get_page(title)
            if page is None:
                page = {"id": new_id("page"), "title": title, "content": content, "tags": tags or "",
                        "created_by": by, "updated_by": by, "created_at": now, "updated_at": now}
                self.execute("INSERT INTO pages (id, title, content, tags, created_by, updated_by, created_at, "
                             "updated_at) VALUES (?,?,?,?,?,?,?,?)", tuple(page.values()))
                return page, True
            if append and page["content"]:
                content = page["content"].rstrip() + "\n\n" + content
            page.update(content=content, updated_by=by, updated_at=now)
            if tags is not None:
                page["tags"] = tags
            self.execute("UPDATE pages SET content = ?, tags = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                         (page["content"], page["tags"], by, now, page["id"]))
            return page, False

    def delete_page(self, pid: str) -> bool:
        return self.execute("DELETE FROM pages WHERE id = ?", (pid,)).rowcount > 0

    # -- finances -----------------------------------------------------------------
    def add_finance_transaction(self, *, kind: str, amount_cents: int, currency: str, date: str,
                                client: str = "", category: str = "", note: str = "") -> dict:
        now = time.time()
        row = {"id": new_id("txn"), "kind": kind, "amount_cents": amount_cents, "currency": currency,
               "date": date, "client": client, "category": category, "note": note, "created_at": now}
        self.execute("INSERT INTO finance_transactions (id,kind,amount_cents,currency,date,client,category,note,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", tuple(row.values()))
        return row

    def list_finance_transactions(self, *, start: str | None = None, end: str | None = None, limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self.all("SELECT * FROM finance_transactions" + where + " ORDER BY date DESC, created_at DESC LIMIT ?",
                        (*params, limit))

    def finance_totals(self, *, start: str | None = None, end: str | None = None) -> dict[str, dict[str, int]]:
        clauses, params = [], []
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.all("SELECT currency, kind, COALESCE(SUM(amount_cents),0) AS amount FROM finance_transactions" +
                        where + " GROUP BY currency, kind", params)
        totals = {c: {"income_cents": 0, "expense_cents": 0, "net_cents": 0} for c in ("MAD", "EUR")}
        for row in rows:
            key = "income_cents" if row["kind"] == "income" else "expense_cents"
            totals[row["currency"]][key] = row["amount"]
        for value in totals.values():
            value["net_cents"] = value["income_cents"] - value["expense_cents"]
        return totals

    # -- journal d'activité --------------------------------------------------
    def add_event(self, category: str, label: str, status: str, *, duration_ms: int | None = None,
                  detail: str | None = None, run_id: str | None = None,
                  conversation_id: str | None = None) -> dict:
        now = time.time()
        cur = self.execute(
            "INSERT INTO events (ts, category, label, status, duration_ms, detail, run_id, conversation_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (now, category, label, status, duration_ms, detail, run_id, conversation_id))
        event_id = cur.lastrowid
        if event_id and event_id % 200 == 0:
            self.execute("DELETE FROM events WHERE id <= ?", (event_id - MAX_EVENTS,))
        return {"id": event_id, "ts": now, "category": category, "label": label, "status": status,
                "duration_ms": duration_ms, "detail": detail, "run_id": run_id,
                "conversation_id": conversation_id}

    def list_events(self, limit: int = 200) -> list[dict]:
        rows = self.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        rows.reverse()
        return rows
