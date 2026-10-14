"""Configuration d'Atlas.

La configuration statique vit dans ``donnees/atlas.env`` (format CLÉ=valeur).
Les préférences modifiables depuis l'interface vivent en base (table ``preferences``).
Aucun secret de fournisseur de modèle n'est stocké ici : seuls la clé de
l'API Hermes locale (générée pour Atlas) et, en option, le jeton du tableau
de bord Hermes le sont.
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT = 8765


def default_data_dir() -> Path:
    env = os.environ.get("ATLAS_DATA_DIR", "").strip()
    return Path(env).expanduser() if env else PROJECT_ROOT / "donnees"


def default_hermes_home() -> Path:
    """Même règle que Hermes : %LOCALAPPDATA%\\hermes sous Windows, ~/.hermes ailleurs."""
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Met à jour (ou ajoute) des clés en conservant les commentaires existants."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    remaining = dict(updates)
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(raw)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


@dataclass
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    lan: bool = False
    https: bool = False
    hermes_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_home: Path = field(default_factory=default_hermes_home)
    hermes_turn_timeout_s: int = 1800
    stt_provider: str = "local_whisper"  # local_whisper | hermes_dashboard | off
    whisper_model: str = "base"
    tts_provider: str = "piper"  # piper | edge | hermes_dashboard | off
    tts_voice: str = ""  # vide = lue dans la config Hermes, sinon fr_FR-tom-medium (Piper)
    hermes_dashboard_url: str = "http://127.0.0.1:9119"
    hermes_dashboard_token: str = ""
    daily_skill: str = ""  # nom exact de la compétence « Suivi quotidien » (vide = détection)
    demo: bool = False
    mcp_port: int = 8766
    mcp_token: str = ""
    max_message_chars: int = 20000
    max_audio_bytes: int = 12 * 1024 * 1024

    @property
    def env_path(self) -> Path:
        return self.data_dir / "atlas.env"

    @property
    def db_path(self) -> Path:
        return self.data_dir / ("atlas-demo.db" if self.demo else "atlas.db")

    @property
    def cert_dir(self) -> Path:
        return self.data_dir / "certificats"

    @property
    def scheme(self) -> str:
        return "https" if self.https else "http"


def load_settings(data_dir: Path | None = None) -> Settings:
    data_dir = data_dir or default_data_dir()
    env = parse_env_file(data_dir / "atlas.env")
    # Les variables d'environnement du processus ont priorité (pratique pour les tests).
    for key in list(env.keys()) + [
        "ATLAS_DEMO", "ATLAS_PORT", "ATLAS_MCP_PORT", "ATLAS_LAN", "HERMES_URL", "HERMES_API_KEY", "ATLAS_STT", "ATLAS_TTS",
    ]:
        if os.environ.get(key):
            env[key] = os.environ[key]
    lan = _bool(env.get("ATLAS_LAN"))
    https_raw = (env.get("ATLAS_HTTPS") or "auto").lower()
    https = lan if https_raw == "auto" else _bool(https_raw)
    hermes_home_raw = env.get("HERMES_HOME", "").strip()
    return Settings(
        data_dir=data_dir,
        host="0.0.0.0" if lan else "127.0.0.1",
        port=int(env.get("ATLAS_PORT") or DEFAULT_PORT),
        lan=lan,
        https=https,
        hermes_url=(env.get("HERMES_URL") or "http://127.0.0.1:8642").rstrip("/"),
        hermes_api_key=env.get("HERMES_API_KEY", ""),
        hermes_home=Path(hermes_home_raw).expanduser() if hermes_home_raw else default_hermes_home(),
        hermes_turn_timeout_s=int(env.get("HERMES_TURN_TIMEOUT_S") or 1800),
        stt_provider=(env.get("ATLAS_STT") or "local_whisper").strip().lower(),
        whisper_model=env.get("ATLAS_WHISPER_MODEL") or "base",
        tts_provider=(env.get("ATLAS_TTS") or "piper").strip().lower(),
        tts_voice=env.get("ATLAS_TTS_VOICE", "").strip(),
        hermes_dashboard_url=(env.get("HERMES_DASHBOARD_URL") or "http://127.0.0.1:9119").rstrip("/"),
        hermes_dashboard_token=env.get("HERMES_DASHBOARD_TOKEN", ""),
        daily_skill=env.get("ATLAS_DAILY_SKILL", "").strip(),
        demo=_bool(env.get("ATLAS_DEMO")),
        mcp_port=int(env.get("ATLAS_MCP_PORT") or 8766),
        mcp_token=env.get("ATLAS_MCP_TOKEN", "").strip(),
    )


ENV_TEMPLATE = """# Configuration d'Atlas — fichier local, ne pas partager.
# Après modification, relancer Atlas.

# Port de l'interface web.
ATLAS_PORT={port}

# 0 = accessible uniquement depuis ce PC (recommandé au départ).
# 1 = accessible depuis le réseau local (Mac…), en HTTPS avec certificat local.
ATLAS_LAN=0
ATLAS_HTTPS=auto

# Connexion au serveur API officiel d'Hermes (hermes gateway).
HERMES_URL=http://127.0.0.1:8642
# Doit être identique à API_SERVER_KEY dans la configuration d'Hermes.
HERMES_API_KEY={hermes_key}
# Durée maximale d'une tâche Hermes (secondes) avant arrêt demandé.
HERMES_TURN_TIMEOUT_S=1800

# Transcription vocale : local_whisper | hermes_dashboard | off
ATLAS_STT=local_whisper
ATLAS_WHISPER_MODEL=base
# Synthèse vocale : piper (local) | edge | hermes_dashboard | off
ATLAS_TTS=piper
# Vide = voix lue dans la configuration Hermes (tts.edge.voice), sinon fr-FR-HenriNeural.
ATLAS_TTS_VOICE=

# Optionnel : relais vers le tableau de bord Hermes (hermes dashboard) pour la voix.
HERMES_DASHBOARD_URL=http://127.0.0.1:9119
HERMES_DASHBOARD_TOKEN=

# Nom exact de la compétence de suivi quotidien (vide = détection automatique).
ATLAS_DAILY_SKILL=

# Outils d'Atlas pour Hermes (tâches, priorités, notes) — serveur MCP local, jamais exposé au réseau.
ATLAS_MCP_PORT=8766
ATLAS_MCP_TOKEN={mcp_token}

# Mode démonstration (réponses simulées, base séparée). Ne pas activer en usage normal.
ATLAS_DEMO=0
"""


def ensure_env_file(data_dir: Path) -> tuple[Path, bool]:
    """Crée ``atlas.env`` s'il n'existe pas. Retourne (chemin, créé)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "atlas.env"
    if path.exists():
        return path, False
    path.write_text(
        ENV_TEMPLATE.format(port=DEFAULT_PORT, hermes_key=secrets.token_hex(32), mcp_token=secrets.token_hex(24)),
        encoding="utf-8")
    return path, True


def ensure_mcp_token(data_dir: Path) -> str:
    """Ajoute le jeton des outils MCP à une configuration créée par une version précédente."""
    path = data_dir / "atlas.env"
    values = parse_env_file(path)
    if values.get("ATLAS_MCP_TOKEN"):
        return values["ATLAS_MCP_TOKEN"]
    token = secrets.token_hex(24)
    update_env_file(path, {"ATLAS_MCP_PORT": values.get("ATLAS_MCP_PORT") or "8766", "ATLAS_MCP_TOKEN": token})
    return token
