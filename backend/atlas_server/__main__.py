"""Ligne de commande d'Atlas.

    python -m atlas_server init          # prépare donnees/ (config, base, code d'accès)
    python -m atlas_server serve         # démarre le serveur
    python -m atlas_server verifier      # diagnostic de la connexion à Hermes (aucun appel au modèle)
    python -m atlas_server nouveau-code  # nouveau code d'accès (déconnecte les appareils)
    python -m atlas_server sauvegarder   # copie de la base + export des notes et priorités
    python -m atlas_server certificats   # (re)génère les certificats HTTPS locaux
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from . import __version__
from .auth import ensure_access_code, set_access_code
from .config import default_data_dir, ensure_env_file, load_settings
from .db import Database


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def cmd_init(_args) -> int:
    data_dir = default_data_dir()
    env_path, created = ensure_env_file(data_dir)
    settings = load_settings(data_dir)
    db = Database(settings.db_path)
    code = ensure_access_code(db, data_dir)
    db.close()
    print(f"Dossier de données : {data_dir}")
    print(("Configuration créée : " if created else "Configuration existante conservée : ") + str(env_path))
    if code:
        print(f"Code d'accès : {code}  (aussi enregistré dans {data_dir / 'CODE-ACCES.txt'})")
    return 0


def cmd_new_code(_args) -> int:
    settings = load_settings()
    db = Database(settings.db_path)
    code = set_access_code(db, settings.data_dir)
    db.close()
    print(f"Nouveau code d'accès : {code}")
    print("Tous les appareils devront se reconnecter avec ce code.")
    return 0


def cmd_backup(_args) -> int:
    settings = load_settings()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_dir = settings.data_dir / "sauvegardes"
    db = Database(settings.db_path)
    db.backup_to(target_dir / f"atlas-{stamp}.db")
    export = {"exported_at": time.time(), "version": __version__, "notes": db.list_notes(),
              "priorities": db.list_priorities()}
    (target_dir / f"atlas-export-{stamp}.json").write_text(json.dumps(export, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
    db.close()
    print(f"Sauvegarde créée dans {target_dir}")
    return 0


def cmd_certs(_args) -> int:
    from .certs import ensure_server_certificate, local_ip_addresses
    settings = load_settings()
    crt, _key, regenerated = ensure_server_certificate(settings.cert_dir)
    print(("Certificat régénéré : " if regenerated else "Certificat à jour : ") + str(crt))
    print(f"Autorité à installer sur le Mac : {settings.cert_dir / 'atlas-ca.crt'}")
    for ip in local_ip_addresses():
        print(f"Adresse depuis le Mac : https://{ip}:{settings.port}")
    return 0


def cmd_check(_args) -> int:
    from .hermes_adapter import HermesAdapter
    settings = load_settings()
    print(f"Atlas {__version__} — Hermes attendu sur {settings.hermes_url}")
    if settings.demo:
        print("ATTENTION : mode démonstration activé (ATLAS_DEMO=1).")

    async def run() -> int:
        adapter = HermesAdapter(settings.hermes_url, settings.hermes_api_key)
        status = await adapter.probe()
        await adapter.close()
        print(f"  Serveur API joignable : {'oui' if status.reachable else 'non'}")
        print(f"  Clé acceptée          : {'oui' if status.authenticated else 'non'}")
        print(f"  Mode d'intégration    : {status.mode}")
        if status.model_alias:
            print(f"  Nom annoncé           : {status.model_alias}")
        if status.error:
            print(f"  Problème              : {status.error}")
        return 0 if status.authenticated else 1

    return asyncio.run(run())


def cmd_serve(args) -> int:
    import uvicorn
    from .app import create_app

    settings = load_settings()
    if not settings.env_path.exists():
        cmd_init(args)
        settings = load_settings()
    ssl_kwargs = {}
    if settings.https:
        from .certs import ensure_server_certificate
        crt, key, _ = ensure_server_certificate(settings.cert_dir)
        ssl_kwargs = {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}
    pid_path = settings.data_dir / "atlas.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    code_file = settings.data_dir / "CODE-ACCES.txt"
    print(f"Atlas {__version__} démarre sur {settings.scheme}://{'localhost'}:{settings.port}")
    if settings.lan:
        from .certs import local_ip_addresses
        for ip in local_ip_addresses():
            print(f"  Depuis le réseau local : {settings.scheme}://{ip}:{settings.port}")
    if code_file.exists():
        print("  " + code_file.read_text(encoding="utf-8").strip().replace("\n\n", " "))
    print("Pour arrêter : Ctrl+C dans cette fenêtre.")
    try:
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning",
                    ws="websockets", proxy_headers=False, server_header=False, **ssl_kwargs)
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(prog="atlas_server", description="Serveur de l'interface Atlas")
    sub = parser.add_subparsers(dest="command")
    for name, func in (("init", cmd_init), ("serve", cmd_serve), ("verifier", cmd_check),
                       ("nouveau-code", cmd_new_code), ("sauvegarder", cmd_backup), ("certificats", cmd_certs)):
        sub.add_parser(name).set_defaults(func=func)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = cmd_serve
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
