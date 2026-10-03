# Démarrer Atlas sur le PC Windows

Ce guide part du principe qu'Hermes fonctionne déjà chez toi dans PowerShell (c'est le cas).
Compte 15 minutes la première fois.

## 1. Ce qu'il faut

| Élément | Pourquoi | Vérification |
|---|---|---|
| Hermes Agent installé et configuré (`hermes model` déjà fait) | c'est le cerveau d'Atlas | `hermes --version` dans PowerShell |
| Python **3.11, 3.12 ou 3.13** (indépendant de celui d'Hermes) | fait tourner le serveur Atlas | `py -3.12 --version` |
| Le dossier `Atlas` (ce projet) | | par exemple `C:\Users\JARVIS\Atlas` |
| Connexion Internet (installation, voix Henri) | | |

Pas besoin de droits administrateur, de Docker ni de Node.js.

**Installer Python s'il manque** — dans PowerShell :

```powershell
winget install -e --id Python.Python.3.12 --scope user
```

(ou télécharge-le sur python.org et coche « Add python.exe to PATH »). Ferme puis rouvre PowerShell ensuite.

## 2. Placer le dossier

Mets le dossier du projet où tu veux, de préférence **sans le mélanger** avec l'ancienne expérience
`C:\Users\JARVIS\Desktop\JARVIS` (elle n'est ni utilisée ni modifiée par Atlas). Exemple :

```
C:\Users\JARVIS\Atlas
```

Si tu as téléchargé un fichier ZIP : clic droit sur le ZIP → Propriétés → coche « Débloquer » → OK,
**avant** de l'extraire (sinon Windows peut bloquer les scripts).

## 3. Installer (une seule fois)

Double-clic sur **`Installer-Atlas.cmd`**. Le script :

1. cherche Python ;
2. crée un environnement isolé `.venv` dans le dossier (rien n'est installé ailleurs) ;
3. installe les dépendances (versions figées) ;
4. te propose la **transcription vocale locale** (recommandé : ta voix reste sur le PC) ;
5. crée le dossier `donnees` : configuration, base de données, **code d'accès** ;
6. te propose d'**activer le serveur API d'Hermes** (voir ci-dessous), puis vérifie la connexion.

### Ce que l'installation change dans Hermes (avec ton accord)

Le serveur API d'Hermes est désactivé par défaut. Atlas en a besoin. Le script :

- sauvegarde d'abord `%LOCALAPPDATA%\hermes\.env` en `.env.avant-atlas-AAAAMMJJ-HHMMSS` ;
- exécute `hermes config set API_SERVER_ENABLED true` et `hermes config set API_SERVER_KEY <clé>` ;
- partage cette clé avec Atlas (`donnees\atlas.env`) sans l'afficher ;
- propose `hermes gateway restart` pour la prendre en compte.

Rien d'autre n'est modifié : ni le modèle, ni le fournisseur ChatGPT/Codex, ni la mémoire, ni
`SOUL.md`, ni Telegram. Le serveur API écoute **uniquement sur ce PC** (`127.0.0.1:8642`).
Si Hermes avait déjà une clé API, Atlas la réutilise et Hermes n'est pas modifié.

**Faire la même chose à la main** (si tu as refusé) :

```powershell
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY <la valeur de HERMES_API_KEY dans donnees\atlas.env>
hermes gateway restart
```

Pour annuler : remets la sauvegarde `.env.avant-atlas-…` à la place de `.env`, puis `hermes gateway restart`.

## 4. Lancer Atlas au quotidien

1. Hermes doit tourner : c'est la « passerelle » (`hermes gateway`), la même qui sert Telegram.
   Vérifie avec `hermes gateway status`. Si elle est arrêtée : `hermes gateway start`.
2. Double-clic sur **`Atlas.cmd`**. Une fenêtre « Atlas — serveur » s'ouvre (**laisse-la ouverte**)
   puis le navigateur s'ouvre sur `http://localhost:8765`.
3. La première fois, saisis le **code d'accès** (affiché dans la fenêtre et dans `donnees\CODE-ACCES.txt`).
   Le navigateur le retient 90 jours.

## 5. Arrêter

- Ferme la fenêtre « Atlas — serveur » (ou Ctrl+C dedans), **ou**
- `scripts\windows\Arreter-Atlas.ps1` (clic droit → « Exécuter avec PowerShell »).

Hermes continue de tourner (Telegram reste disponible).

## 6. Sauvegarder

Clic droit sur `scripts\windows\Sauvegarder-Atlas.ps1` → « Exécuter avec PowerShell ».
Copie cohérente de la base + export des notes et priorités dans `donnees\sauvegardes`.
Pense à copier ce dossier sur une clé USB de temps en temps. Dans l'interface :
Réglages → Données → export JSON ou Markdown.

La mémoire et la personnalité d'Hermes ne sont **pas** dans Atlas : sauvegarde aussi
`%LOCALAPPDATA%\hermes` (ou utilise les outils de sauvegarde d'Hermes).

## 7. Mettre à jour

Ferme Atlas, puis `scripts\windows\Mettre-a-jour-Atlas.ps1` (sauvegarde automatique avant).
Le dossier `donnees` n'est jamais touché ; la base évolue automatiquement au démarrage.

Hermes se met à jour séparément avec `hermes update`. Si Atlas affiche « mode compatibilité »
dans Réglages → Connexion, une mise à jour d'Hermes débloque l'arrêt des tâches et les approbations.

## 8. Démarrage automatique avec Windows (désactivé par défaut)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\Demarrage-Auto.ps1 -Activer
powershell -ExecutionPolicy Bypass -File scripts\windows\Demarrage-Auto.ps1 -Desactiver
```

Pour Hermes : `hermes gateway install` (tâche planifiée à l'ouverture de session, sans admin).

## 9. Ports et accès

| Port | Qui | Accessible depuis |
|---|---|---|
| 8765 | Atlas (interface) | ce PC seulement ; le réseau local si tu actives le mode réseau |
| 8642 | Serveur API d'Hermes | ce PC seulement (`127.0.0.1`) — ne pas l'ouvrir |

Aucune ouverture vers Internet, aucune redirection de port n'est nécessaire.

## 10. Vérifications à faire sur ton PC

Je n'ai pas eu accès à ton PC : ces points sont à vérifier une fois.
`scripts\windows\Verifier-Hermes.ps1` fait le diagnostic en lecture seule (aucun appel au modèle) :

1. **Dossier Hermes** : `%LOCALAPPDATA%\hermes` contient `config.yaml`, `.env`, `SOUL.md`,
   `memories\MEMORY.md`, `skills`. Si Hermes utilise un autre dossier, définis `HERMES_HOME`.
2. **Version** : `hermes --version`. Le diagnostic doit afficher « Mode d'intégration : sessions ».
   Si c'est « chat_completions », Atlas fonctionne mais sans arrêt de tâche ni approbation : `hermes update`.
3. **Modèle** : Atlas n'impose aucun modèle. Après le premier échange, Réglages → Connexion affiche
   le fournisseur/modèle **réellement utilisé, rapporté par Hermes**. Il doit correspondre à ce que tu as
   choisi avec `hermes model`.
4. **Voix** : Réglages → Voix affiche la voix lue dans la configuration d'Hermes (`tts.edge.voice`),
   sinon `fr-FR-HenriNeural`.
5. **Compétence Suivi quotidien** : le bouton « Préparer ma journée » indique le nom trouvé. S'il en
   trouve plusieurs ou aucune, écris son nom exact dans `donnees\atlas.env` : `ATLAS_DAILY_SKILL=…`.

## 11. Problèmes fréquents

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Hermes indisponible — ne répond pas sur http://127.0.0.1:8642 » | passerelle arrêtée ou API non activée | `hermes gateway status`, puis section 3 |
| « Hermes refuse la clé » | clés différentes | `HERMES_API_KEY` (Atlas) doit égaler `API_SERVER_KEY` (Hermes) ; relancer l'installateur |
| « Limite d'utilisation atteinte » | quota de l'abonnement | attendre ; rien n'est perdu ; aucun autre fournisseur n'est utilisé |
| « Le fournisseur refuse la connexion » | session ChatGPT/Codex expirée | `hermes model` (ou `hermes auth`) dans PowerShell |
| Bouton « Parler » grisé | transcription non installée | relancer `Installer-Atlas.cmd` et accepter la transcription locale |
| Première dictée lente | téléchargement du modèle Whisper (~500 Mo, une fois) | patienter ; ensuite quelques secondes |
| Pas de son | lecture automatique bloquée | bouton « ▶ Écouter » qui apparaît, ou icône haut-parleur sous la réponse |
| Port 8765 déjà utilisé | autre programme | changer `ATLAS_PORT` dans `donnees\atlas.env` |

## 12. Économie de ton abonnement

- Le modèle n'est appelé **que** lorsque tu envoies un message (ou cliques « Préparer ma journée »).
- Horloge, notes, priorités, animations, activité : **aucun** appel au modèle.
- Les notes ne sont jointes que si tu cliques « Utiliser dans la conversation ».
- Aucune relance automatique, aucun fournisseur de secours payant.
- À savoir : Hermes génère lui-même un **titre** pour chaque conversation avec un petit appel
  auxiliaire (son réglage `auxiliary.title_generation`). Atlas ne le modifie pas. Pour le couper :
  `hermes config set auxiliary.title_generation.model_upgrade_enabled false`.
