# Bilan des tests et limites (version 0.1.0)

Environnement de développement : Linux, sans accès à ton PC Windows ni à ton Mac. Pour tester
l'intégration réellement, j'ai installé **Hermes Agent** depuis son dépôt officiel (commit du
2 octobre 2026) avec un `HERMES_HOME` de test, une personnalité `SOUL.md` de test, et un **faux
modèle** local compatible OpenAI (`backend/tests/fake_llm.py`) à la place de ChatGPT/Codex — pour
ne rien dépenser et pouvoir provoquer quota, outils et approbations à volonté.

## 1. Tests exécutés et réussis

### Intégration Atlas ↔ vrai Hermes — `backend/tests/integration_hermes.py` : 29/29 ✅

| # | Vérification | Résultat |
|---|---|---|
| 1 | Démarrage sans erreur | ✅ |
| 3 | Réponse provenant réellement d'Hermes (serveur API, mode sessions) | ✅ |
| – | Personnalité `SOUL.md` présente dans le prompt reçu par le modèle | ✅ |
| – | Contexte date/fuseau transmis | ✅ |
| – | Réponse affichée progressivement (10 fragments) | ✅ |
| – | Modèle réellement utilisé rapporté par Hermes | ✅ |
| 6 | Continuité : même session Hermes, historique tenu par Hermes (4 messages) | ✅ |
| 7 | Doublon (même `request_id`) jamais ré-exécuté (0 appel modèle de plus) | ✅ |
| – | Deuxième envoi pendant une exécution → « occupé » | ✅ |
| – | Outil réel (`terminal`) visible dans l'activité ; états orbe réfléchir → agir → repos | ✅ |
| – | Arrêt d'une tâche en cours → « annulée » | ✅ |
| – | Approbation demandée par Hermes affichée ; refus respecté (fichier témoin intact) | ✅ |
| 9 | Fournisseur qui refuse (HTTP 429 quota) → message « limite atteinte » | ✅ |
| 17 | Aucun appel au modèle pendant 35 s de repos avec page connectée | ✅ |
| 4, 5 | Notes et priorités conservées après arrêt/redémarrage | ✅ |
| 6 | Conversation retrouvée après redémarrage | ✅ |
| 14 | Redémarrage pendant une réponse → « interrompue », aucune relance | ✅ |
| 8 | Hermes arrêté → état clair, envoi refusé proprement (le brouillon reste) | ✅ |
| – | Clé API refusée par Hermes détectée | ✅ |
| 15 | Sans connexion → 401 ; mauvais code refusé ; origine étrangère → 403 ; hôte inconnu → 421 ; WebSocket étranger refusé | ✅ |
| 16 | Aucun secret (clé Hermes, clé fournisseur) dans les données envoyées au navigateur | ✅ |

### Interface dans Chromium — `backend/tests/ui_e2e.cjs` : 17/17 ✅

Connexion ; message → réponse réelle ; **double envoi** (Entrée ×2 + clic) → un seul message (7) ;
carte d'approbation + orbe « J'attends ton accord » ; **micro** (micro simulé par Chromium) →
WAV 16 kHz reçu par le serveur et décodé ; texte reconnu affiché et envoyé ; réponse **lue sur
l'appareil demandeur** avec orbe « Je parle » ; « Couper la voix » → retour au repos (12) ; note
enregistrée automatiquement ; priorité ajoutée et cochée à la main ; activité réelle ; réglages ;
**micro refusé** → message clair (10) ; **synthèse indisponible** → message, texte conservé (11) ;
affichage **mobile 390 px** sans débordement ; aucune erreur JavaScript. Captures aussi en
1440×900 et 1024×768 (18).

⚠️ Dans ce test, les deux appels au service vocal (transcription, synthèse) étaient **simulés**
par le navigateur de test : leurs vrais moteurs ne sont pas joignables depuis mon environnement
(voir §3). Tout le reste de la chaîne vocale est réel.

### Accès réseau HTTPS (simulation du Mac)
- Certificat serveur signé par « Atlas CA », valable pour l'IP et le nom de la machine (≤ 825 jours, exigence macOS) : validé par `curl --cacert` ✅
- Sans confiance dans le certificat → connexion refusée par le client ✅
- Navigateur sur `https://<nom-de-machine>:port` : contexte sécurisé ✅, chat ✅, **micro actif** ✅, cookie `Secure + HttpOnly + SameSite=Strict` ✅

### Tests unitaires — `python -m pytest` : 26/26 ✅
Configuration, migrations SQLite, code d'accès (jamais stocké en clair), limitation des essais,
expurgation des secrets, fuseaux, classification des erreurs (quota/authentification), nettoyage du
texte pour la voix, décodage/rééchantillonnage WAV (dont un enregistrement réel du navigateur),
certificats, adaptateur Hermes (mode sessions, mode de repli, mauvaise clé, Hermes éteint, flux
coupé), règles des priorités (3 max, réordonnancement), conflit d'édition des notes, validation des
réglages, export, rejet des formats audio non acceptés.

### Scripts PowerShell
Analyse syntaxique des 10 scripts avec l'analyseur de PowerShell 7 ✅ ; fonctions de lecture /
écriture de `.env` testées (commentaires et autres clés préservés) ✅.

## 2. Corrigé pendant les tests
- Réglages : le modèle affiché n'était pas mis à jour après un échange → corrigé.
- `/v1/skills` répond une erreur 500 dans la version de développement d'Hermes testée (bug amont,
  paramètre `include_editorial`) → Atlas lit alors, en lecture seule, les dossiers `skills` d'Hermes.

## 3. Non testé ici — à vérifier chez toi

| Point | Pourquoi | Comment vérifier |
|---|---|---|
| Ton Hermes réel (version, `%LOCALAPPDATA%\hermes`, fournisseur ChatGPT/Codex, « GPT-6 Luna ») | pas d'accès à ton PC | `scripts\windows\Verifier-Hermes.ps1`, puis un « Bonjour » ; Réglages → Connexion affiche le modèle rapporté par Hermes |
| Ta vraie personnalité / mémoire | idem | demande « Qui es-tu ? » et « Que sais-tu de moi ? » |
| Compétence « Suivi quotidien » | idem | le bouton « Préparer ma journée » doit afficher son nom |
| Voix Henri (Edge TTS) | service Microsoft bloqué par le réseau de mon environnement | dicter une phrase, écouter la réponse |
| Transcription faster-whisper (modèle réel) | téléchargement du modèle bloqué ici | première dictée (téléchargement ~500 Mo, puis quelques secondes par phrase sur l'i5-7200U — durée non mesurée) |
| Windows : installation, double-clic, pare-feu, PowerShell 5.1 | pas de Windows ici | suivre DEMARRAGE-WINDOWS.md |
| Mac (Safari 13 / Chrome 116 / Firefox ESR 115 sur High Sierra) | pas de Mac ici | procédure de ACCES-MAC.md |
| Consommation mémoire réelle sur le Lenovo | non mesurée | Gestionnaire des tâches : processus `python` d'Atlas |

**Le vocal depuis le Mac n'est pas considéré comme terminé** tant que la procédure de test du
guide Mac n'a pas été faite sur ton Mac.

## 4. Limites connues de cette version
- Une seule réponse à la fois (volontaire, pour la fiabilité).
- Pas d'arrêt ni d'approbation depuis l'interface si Hermes est trop ancien (mode compatibilité) : `hermes update`.
- Si la connexion Atlas ↔ Hermes est coupée en pleine réponse, Hermes arrête la tâche (pas de reprise).
- Une approbation non répondue est refusée par Hermes après son délai.
- Atlas ne modifie pas encore les priorités sur ordre d'Atlas lui-même (prévu via un outil avec confirmation structurée) ; une proposition devient une priorité par le bouton drapeau.
- Pas de mot d'activation, pas d'écoute permanente, pas d'accès depuis Internet (volontairement).
- Edge TTS est un service en ligne gratuit de Microsoft, sans garantie de disponibilité.
- Hermes fait un petit appel auxiliaire par échange pour titrer ses sessions (réglage d'Hermes, voir DEMARRAGE-WINDOWS.md §12).

## 5. Refaire les tests
```bash
cd backend
python -m pytest -q                                    # unitaires
python tests/fake_llm.py 9900 &                        # faux modèle
HERMES_HOME=/chemin/test hermes gateway run &          # Hermes de test (voir en-tête de integration_hermes.py)
python tests/integration_hermes.py <API_SERVER_KEY>    # intégration
CODE=… ATLAS_URL=http://localhost:8767 node tests/ui_e2e.cjs   # interface (Playwright)
```
