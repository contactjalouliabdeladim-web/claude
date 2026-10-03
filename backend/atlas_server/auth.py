"""Authentification des appareils (un seul utilisateur).

- Un « code d'accès » est généré à l'installation. Seule son empreinte (scrypt)
  est en base ; le code en clair est écrit dans ``donnees/CODE-ACCES.txt`` pour
  que le script de lancement puisse l'afficher sur le PC Windows.
- Une connexion réussie crée un cookie de session aléatoire (HttpOnly,
  SameSite=Strict, Secure en HTTPS). Seule l'empreinte SHA-256 du jeton est stockée.
- Les tentatives sont limitées par adresse IP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from .db import Database

COOKIE_NAME = "atlas_session"
SESSION_TTL_S = 90 * 24 * 3600
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans caractères ambigus (0/O, 1/I)


def generate_access_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _hash_code(code: str, salt: bytes) -> str:
    digest = hashlib.scrypt(normalize_code(code).encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def set_access_code(db: Database, data_dir: Path, code: str | None = None) -> str:
    code = code or generate_access_code()
    db.set_secret("access_code_hash", _hash_code(code, secrets.token_bytes(16)))
    # Changer le code déconnecte tous les appareils.
    db.execute("DELETE FROM auth_sessions")
    (data_dir / "CODE-ACCES.txt").write_text(
        "Code d'accès Atlas (à saisir une fois par appareil) :\n\n    " + code + "\n", encoding="utf-8")
    return code


def ensure_access_code(db: Database, data_dir: Path) -> str | None:
    """Crée un code au premier démarrage. Retourne le nouveau code, ou None s'il existait."""
    if db.get_secret("access_code_hash"):
        return None
    return set_access_code(db, data_dir)


def verify_code(db: Database, code: str) -> bool:
    stored = db.get_secret("access_code_hash")
    if not stored or "$" not in stored:
        return False
    salt_b64, _ = stored.split("$", 1)
    candidate = _hash_code(code, base64.b64decode(salt_b64))
    return hmac.compare_digest(candidate, stored)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Database, device_label: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute("INSERT INTO auth_sessions (token_hash, device_label, created_at, last_seen, expires_at) "
               "VALUES (?,?,?,?,?)", (_token_hash(token), device_label[:80] or "Appareil", now, now,
                                      now + SESSION_TTL_S))
    return token


def check_session(db: Database, token: str | None) -> dict | None:
    if not token or len(token) > 200:
        return None
    row = db.one("SELECT * FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
    if not row:
        return None
    now = time.time()
    if row["expires_at"] < now:
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (row["token_hash"],))
        return None
    if now - row["last_seen"] > 300:
        db.execute("UPDATE auth_sessions SET last_seen = ? WHERE token_hash = ?", (now, row["token_hash"]))
    return row


def delete_session(db: Database, token: str | None) -> None:
    if token:
        db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))


class LoginRateLimiter:
    """5 tentatives par minute et 20 par heure, par adresse IP."""

    def __init__(self) -> None:
        self._attempts: dict[str, deque] = defaultdict(deque)

    def allowed(self, ip: str) -> bool:
        now = time.monotonic()
        attempts = self._attempts[ip]
        while attempts and now - attempts[0] > 3600:
            attempts.popleft()
        last_minute = sum(1 for t in attempts if now - t < 60)
        return last_minute < 5 and len(attempts) < 20

    def record_failure(self, ip: str) -> None:
        self._attempts[ip].append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._attempts.pop(ip, None)
