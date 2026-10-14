# Atlas — interface de ton assistant personnel

Atlas est l'interface web (écran + voix) de ton assistant personnel. **Le cerveau reste Hermes
Agent** (Nous Research) installé sur ton PC Windows : même personnalité (`SOUL.md`), même mémoire,
mêmes compétences, mêmes outils, même modèle et même abonnement. Atlas ne crée pas un deuxième
assistant : il se branche sur le **serveur API officiel d'Hermes**.

```
 Navigateur (PC Windows ou Mac)          PC Windows
 ┌──────────────────────────┐   HTTPS   ┌───────────────────────────┐  HTTP local  ┌──────────────────────┐
 │ Interface Atlas          │ ◄───────► │ Serveur Atlas (Python)    │ ◄──────────► │ Hermes Agent          │
 │ orbe · chat · journée    │  WebSocket│ notes, priorités, voix,   │ 127.0.0.1    │ (hermes gateway,      │
 │ micro · lecture vocale   │           │ historique (SQLite)       │ :8642        │  serveur API officiel)│
 └──────────────────────────┘           └───────────────────────────┘              └──────────────────────┘
```

## Démarrage rapide (Windows)

1. Double-clic sur **`Installer-Atlas.cmd`** (une seule fois) et réponds aux questions.
2. Double-clic sur **`Atlas.cmd`** : le navigateur s'ouvre ; saisis le code d'accès affiché.

Guide pas à pas : **[docs/DEMARRAGE-WINDOWS.md](docs/DEMARRAGE-WINDOWS.md)**
Accès depuis le Mac : **[docs/ACCES-MAC.md](docs/ACCES-MAC.md)**
Fonctionnement : **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**
Ce qui a été testé, et ce qui reste à vérifier chez toi : **[docs/TESTS-ET-LIMITES.md](docs/TESTS-ET-LIMITES.md)**

## Ce que fait la première version

- Conversation écrite avec la **vraie** réponse d'Hermes, affichée au fil de l'eau, historique, plusieurs conversations.
- Voix : bouton « Parler » → transcription → Hermes → réponse lue **sur l'appareil qui a parlé**, orbe synchronisée sur le son réel.
- Orbe animée qui reflète l'état réel : disponible, à l'écoute, transcription, réflexion, action (outil Hermes en cours), attente d'accord, parole, erreur, hors ligne.
- Panneau d'activité : uniquement les événements réellement observés (outils lancés/terminés, approbations, voix…).
- Approbations : quand Hermes demande l'autorisation pour une commande sensible, la demande s'affiche ; sans réponse, Hermes refuse.
- Ma journée : heure et date (fuseau `Africa/Casablanca` modifiable), 3 priorités, notes rapides — sans aucun appel au modèle.
- « Préparer ma journée avec Atlas » : demande à Hermes d'utiliser ta compétence de suivi quotidien.
- Accès depuis le Mac sur le réseau local (HTTPS avec certificat local, nécessaire pour le micro).

## Organisation des dossiers

```
Atlas.cmd, Installer-Atlas.cmd   lancement / installation par double-clic
backend/                         serveur Python (FastAPI)
  atlas_server/hermes_adapter.py   ← seul module qui parle à Hermes
  atlas_server/turns.py            conversation : un tour à la fois, anti-doublons
  atlas_server/voice/              transcription et synthèse vocale
  atlas_server/static/             interface compilée (servie par le serveur)
  tests/                           tests unitaires et d'intégration
frontend/                        code source de l'interface (React + TypeScript + Vite)
scripts/windows/                 scripts PowerShell (installer, lancer, arrêter, sauvegarder…)
docs/                            guides en français
donnees/                         (créé à l'installation) config, base, certificats — jamais dans git
```

Développement de l'interface : `cd frontend && npm install && npm run build` (Node.js 18+ requis
uniquement pour modifier l'interface ; l'usage quotidien n'a besoin que de Python).
