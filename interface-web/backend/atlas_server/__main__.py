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
import contextlib
import json
import os
import sys
import time

from . import __version__
from .auth import ensure_access_code, set_access_code
from .config import default_data_dir, ensure_env_file, ensure_mcp_token, load_settings
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
    ensure_mcp_token(data_dir)
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


def _port_free(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def cmd_serve(args) -> int:
    import uvicorn
    from .app import create_app
    from .internal_server import create_internal_app

    settings = load_settings()
    if not settings.env_path.exists():
        cmd_init(args)
    ensure_mcp_token(settings.data_dir)
    settings = load_settings()
    ssl_kwargs = {}
    if settings.https:
        from .certs import ensure_server_certificate
        crt, key, _ = ensure_server_certificate(settings.cert_dir)
        ssl_kwargs = {"ssl_certfile": str(crt), "ssl_keyfile": str(key)}
    pid_path = settings.data_dir / "atlas.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    code_file = settings.data_dir / "CODE-ACCES.txt"
    print(f"Atlas {__version__} démarre sur {settings.scheme}://localhost:{settings.port}")
    if settings.lan:
        from .certs import local_ip_addresses
        for ip in local_ip_addresses():
            print(f"  Depuis le réseau local : {settings.scheme}://{ip}:{settings.port}")
    if code_file.exists():
        print("  " + code_file.read_text(encoding="utf-8").strip().replace("\n\n", " "))
    print("Pour arrêter : Ctrl+C dans cette fenêtre.")

    app = create_app(settings)
    main_server = uvicorn.Server(uvicorn.Config(
        app, host=settings.host, port=settings.port, log_level="warning", ws="websockets-sansio",
        proxy_headers=False, server_header=False, **ssl_kwargs))

    class QuietServer(uvicorn.Server):
        """Serveur secondaire : laisse la gestion de Ctrl+C au serveur principal."""
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    async def run() -> None:
        mcp_server = None
        mcp_task = None
        if settings.mcp_token and _port_free("127.0.0.1", settings.mcp_port):
            mcp_server = QuietServer(uvicorn.Config(
                create_internal_app(app, lambda: settings.mcp_token), host="127.0.0.1", port=settings.mcp_port,
                log_level="warning", lifespan="off", server_header=False))
            mcp_task = asyncio.create_task(mcp_server.serve())
            print(f"  Liaison interne avec les outils d'Atlas : 127.0.0.1:{settings.mcp_port}")
        elif settings.mcp_token:
            print(f"  ATTENTION : port {settings.mcp_port} occupé : les actions d'Atlas (tâches) n'apparaîtront "
                  "qu'après rechargement de la page.")
        try:
            await main_server.serve()
        finally:
            if mcp_server is not None:
                mcp_server.should_exit = True
                await mcp_task

    try:
        asyncio.run(run())
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass
    return 0


def cmd_mcp_stdio(_args) -> int:
    from .mcp_stdio import main as stdio_main
    return stdio_main()


def main(argv: list[str] | None = None) -> int:
    if not (argv or sys.argv[1:])[:1] == ["mcp-stdio"]:
        _utf8_console()
    parser = argparse.ArgumentParser(prog="atlas_server", description="Serveur de l'interface Atlas")
    sub = parser.add_subparsers(dest="command")
    for name, func in (("init", cmd_init), ("serve", cmd_serve), ("verifier", cmd_check),
                       ("nouveau-code", cmd_new_code), ("sauvegarder", cmd_backup), ("certificats", cmd_certs),
                       ("mcp-stdio", cmd_mcp_stdio)):
        sub.add_parser(name).set_defaults(func=func)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = cmd_serve
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
