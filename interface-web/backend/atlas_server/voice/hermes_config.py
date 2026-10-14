"""Lecture SEULE de quelques réglages non secrets de ``config.yaml`` d'Hermes.

On évite une dépendance YAML : seules des clés simples imbriquées sont lues
(ex. ``tts.edge.voice``). Rien n'est jamais écrit dans les fichiers d'Hermes.
"""

from __future__ import annotations

from pathlib import Path


def read_simple_yaml_value(path: Path, dotted: str) -> str | None:
    if not path.is_file():
        return None
    wanted = dotted.split(".")
    stack: list[tuple[int, str]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        if raw.lstrip().startswith("-"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, value = raw.strip().partition(":")
        key = key.strip().strip("'\"")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path_keys = [k for _, k in stack] + [key]
        value = value.split(" #", 1)[0].strip()
        if path_keys == wanted and value:
            return value.strip("'\"")
        if not value:
            stack.append((indent, key))
    return None


def hermes_config_path(hermes_home: Path) -> Path:
    return hermes_home / "config.yaml"


def hermes_tts_settings(hermes_home: Path) -> dict[str, str | None]:
    cfg = hermes_config_path(hermes_home)
    return {
        "provider": read_simple_yaml_value(cfg, "tts.provider"),
        "edge_voice": read_simple_yaml_value(cfg, "tts.edge.voice"),
        "piper_voice": read_simple_yaml_value(cfg, "tts.piper.voice"),
        "piper_length_scale": read_simple_yaml_value(cfg, "tts.piper.length_scale"),
        "stt_provider": read_simple_yaml_value(cfg, "stt.provider"),
    }
