# Atlas (projet Jarvis)

Dossier maître d'Atlas : la copie de référence de tout ce qui fait Atlas,
reprise de l'ancien PC (utilisateur "JARVIS") via la clé USB.
Hermes, lui, travaille dans `C:\Users\jalou\AppData\Local\hermes` ;
on y copie les fichiers d'ici.

| Dossier            | Contenu                                                          | Origine sur la clé |
|--------------------|------------------------------------------------------------------|--------------------|
| `persona\`         | SOUL.md : la personnalité d'Atlas                                | D:\hermes |
| `memoire\`         | MEMORY.md et USER.md : la mémoire d'Atlas                        | D:\hermes\memories |
| `config\`          | .env et atlas.env (CLÉS SECRÈTES, ne jamais partager), config.yaml de l'ancien PC | D:\hermes, D:\Atlas-interface\donnees |
| `skills\`          | tous les skills, dont le skill perso `suivi-quotidien`           | D:\hermes\skills |
| `donnees-hermes\`  | state.db, kanban.db, shared-state.db, response_store.db, cron\   | D:\hermes |
| `interface-web\`   | l'interface web (backend, frontend, scripts, données, modèles de voix) | D:\Atlas-interface |
| `voix\`            | l'ancienne couche vocale et l'ancienne appli                     | D:\JARVIS-Bureau |
| `archives\`        | historique des chats (chat-vault) et journaux                    | D:\Documents, D:\_journaux |

Non copiés, car ils se réinstallent ou se recréent tout seuls :
.venv, node_modules, __pycache__, fichiers .lock, atlas.pid, D:\hermes\tools.

Note : `config\config.yaml` est l'ancienne configuration ; elle contient des
chemins en C:\Users\JARVIS. Elle sert de référence et ne doit pas remplacer
telle quelle la configuration du nouveau Hermes.
