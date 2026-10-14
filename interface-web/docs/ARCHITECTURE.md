# Architecture d'Atlas

## Principe

Atlas = **une interface + un petit serveur** sur le PC Windows. Il n'a ni modèle, ni mémoire, ni
personnalité à lui : tout cela reste dans Hermes. Le serveur Atlas sert l'interface, garde les
données propres au tableau de bord et parle à Hermes par son **API officielle**.

```
Navigateur ──HTTPS/WebSocket──► Serveur Atlas (FastAPI, Python) ──HTTP 127.0.0.1:8642──► Hermes (hermes gateway)
   │                                │                                                  │
   │ interface React compilée       ├─ SQLite : conversations, messages affichés,      ├─ modèle + abonnement
   │ micro (WAV 16 kHz)             │  priorités, notes, préférences, activité        ├─ SOUL.md, mémoire
   │ lecture audio + analyse        ├─ voix : faster-whisper (local) / Edge TTS        ├─ compétences, outils
   │                                └─ hermes_adapter.py (seul lien avec Hermes)        └─ approbations
```

## Choix de l'intégration Hermes

J'ai étudié le code et la documentation d'Hermes (dépôt NousResearch/hermes-agent, octobre 2026).
Hermes propose trois interfaces programmatiques officielles : ACP (IDE), la passerelle JSON-RPC du
TUI, et le **serveur API HTTP** (`gateway/platforms/api_server.py`, doc
`user-guide/features/api-server.md`). J'ai retenu le serveur API car :

- il est documenté pour les interfaces web (« any frontend ») et maintenu ;
- il tourne dans la **même passerelle** que Telegram (`hermes gateway`) : un seul Hermes, une
  seule mémoire, pas de deuxième processus d'agent ;
- il fournit des **sessions côté Hermes**, un **flux d'événements réels** (texte, outils,
  approbations, fin), l'**arrêt** d'une tâche et la **réponse aux approbations** ;
- il exige une clé (`API_SERVER_KEY`) et n'écoute que sur `127.0.0.1` par défaut.

Pas d'automatisation de fenêtre PowerShell, pas de sous-processus à piloter, pas de format de
terminal à analyser.

### Points d'API utilisés (et seulement eux)

| Besoin | Appel | Remarque |
|---|---|---|
| État (sans modèle) | `GET /health`, `GET /v1/capabilities` | toutes les 30 s si une page est ouverte, 2 min sinon |
| Nouvelle conversation | `POST /api/sessions` | une session Hermes par conversation Atlas |
| Envoyer un message | `POST /api/sessions/{id}/chat/stream` | flux SSE : `assistant.delta`, `tool.started/completed/failed`, `approval.request`, `run.completed/failed/cancelled` |
| Arrêter | `POST /v1/runs/{run_id}/stop` | arrêt coopératif |
| Approbation | `POST /v1/runs/{run_id}/approval` | `once`, `session` ou `deny` |
| Vérifier après coupure | `GET /v1/runs/{run_id}` | jamais de relance |
| Compétence du jour | `GET /v1/skills` (sinon lecture seule des dossiers `skills`) | |

**Repli** pour une version d'Hermes sans API de sessions : `POST /v1/chat/completions` en flux avec
l'en-tête `X-Hermes-Session-Id` (Hermes relit lui-même l'historique). Dans ce mode, ni arrêt ni
approbation depuis l'interface (l'interface le signale).

### Ce qu'Atlas envoie à Hermes

1. le texte de ton message (plus, si tu le demandes, les notes jointes) ;
2. un court **contexte éphémère** (`system_message`) : date, heure et fuseau du tableau de bord, et
   « message dicté » pour la voix. Hermes l'ajoute à son propre prompt sans remplacer la personnalité.

Atlas n'envoie jamais tout l'historique : Hermes le garde dans sa session.

## Sessions, appareils et doublons

- **Conversation Atlas** (`conv_…`) ↔ **session Hermes** (`hermes_session_id`). Si Hermes renomme
  la session (compression du contexte), Atlas suit le nouvel identifiant renvoyé.
- **Une seule exécution à la fois** : pendant une réponse, un autre envoi reçoit « Atlas est occupé ».
- **Identifiant de requête** : chaque envoi porte un `request_id` créé par le navigateur. Un même
  identifiant n'est jamais exécuté deux fois (double clic, réseau coupé puis « Réessayer »).
- **Coupure réseau** : si la connexion entre Atlas et Hermes tombe, Hermes interrompt la tâche
  (comportement de son serveur). Atlas consulte l'état du run et marque la réponse « interrompue »,
  sans jamais relancer. Au redémarrage du serveur Atlas, une réponse restée en cours est marquée
  « interrompue ».
- **Plusieurs fenêtres** (PC + Mac) : toutes reçoivent les mêmes mises à jour par WebSocket. La
  voix n'est jouée **que** sur l'appareil qui a fait la demande (`origin_device`).
- **Reconnexion** : la page se reconnecte seule (délai croissant) et recharge l'état ; elle ne
  renvoie aucune commande.

## Voix

| Étape | Où | Détail |
|---|---|---|
| Capture | navigateur | Web Audio → WAV PCM 16 kHz mono (compatible Safari 12/13, sans MediaRecorder) ; 120 s max |
| Envoi | navigateur → Atlas | `POST /api/voice/transcribe`, 12 Mo max, session requise ; l'audio n'est **jamais stocké** |
| Transcription | PC Windows | `local_whisper` (faster-whisper, modèle `small`, int8, déchargé après 10 min) ou relais vers le tableau de bord Hermes, ou `off` |
| Synthèse | PC Windows → Microsoft | `edge-tts`, voix lue dans `config.yaml` d'Hermes (`tts.edge.voice`) sinon `fr-FR-HenriNeural`. **Service en ligne de Microsoft** |
| Lecture | navigateur demandeur | Web Audio + analyseur : l'orbe suit l'amplitude réelle ; si l'analyse est impossible, animation « lecture » simple |

Interrompre la voix (« Couper la voix ») ≠ arrêter la tâche (« Arrêter la tâche »).

## Sécurité

- Écoute sur `127.0.0.1` par défaut ; mode réseau seulement sur demande, et alors en **HTTPS**.
- **Code d'accès** par appareil (empreinte scrypt en base) → cookie de session aléatoire
  `HttpOnly`, `SameSite=Strict`, `Secure` en HTTPS ; 5 essais/minute par adresse.
- Vérification de l'**hôte** (anti « DNS rebinding ») et de l'**origine** (requêtes et WebSocket).
- Limites de taille (messages 20 000 caractères, audio 12 Mo, JSON 256 Ko), validation des entrées.
- Rendu Markdown limité sans HTML ; politique CSP stricte (aucune ressource externe).
- Secrets : la clé du fournisseur reste chez Hermes ; la clé API Hermes reste côté serveur, jamais
  envoyée au navigateur ; journaux d'activité expurgés (jetons, clés, mots de passe).
- Pas de terminal web ni d'exécution de commande : tout passe par Hermes et ses **règles
  d'approbation** (sans réponse, Hermes refuse ; Atlas ne valide jamais à ta place).

## Données

| Donnée | Où | Sauvegarde |
|---|---|---|
| Conversations affichées, notes, priorités, préférences, activité (3000 derniers événements) | `donnees\atlas.db` (SQLite WAL) | `Sauvegarder-Atlas.ps1`, export JSON/Markdown |
| Configuration Atlas, clé API Hermes | `donnees\atlas.env` | à garder privé |
| Code d'accès en clair (pour l'afficher au lancement) | `donnees\CODE-ACCES.txt` | régénérable (`Nouveau-Code.ps1`) |
| Certificats HTTPS | `donnees\certificats` | `atlas-ca.key` ne doit jamais quitter le PC |
| Mémoire, personnalité, sessions, compétences | Hermes (`%LOCALAPPDATA%\hermes`) | outils d'Hermes |

Évolution du schéma : table `schema_version` + migrations numérotées appliquées au démarrage.

## Consommation

Appels au modèle : uniquement quand tu envoies un message. Le reste (horloge, notes, priorités,
animations, surveillance de l'état) n'utilise que des appels locaux sans modèle. Indicateurs : le
nombre de jetons du dernier échange est affiché **seulement s'il est rapporté par Hermes** ; le quota
restant de l'abonnement n'est pas connu, donc pas affiché.

## Faire évoluer

- Tout le dialogue avec Hermes est dans `backend/atlas_server/hermes_adapter.py`.
- Les états de l'orbe viennent de `turns.py` (serveur) et `controller.tsx` (appareil).
- Pistes futures (non développées) : mot d'activation, accès Internet, synchronisation calendrier,
  modification des priorités par Atlas via un outil MCP avec confirmation structurée.
