# Utiliser Atlas depuis le Mac (réseau local)

Le Mac sert seulement d'écran, de micro et de haut-parleur. Hermes, sa mémoire et les données
d'Atlas restent sur le PC Windows : rien à installer sur le Mac à part un certificat.

**Pourquoi HTTPS ?** Les navigateurs n'autorisent le micro que sur une page sécurisée (HTTPS, ou
`localhost` sur la machine elle-même). Sur `http://192.168.x.x` le chat marcherait, mais pas le micro.
Atlas crée donc sa propre petite autorité de certification, que tu approuves une fois sur le Mac.

## Étape 1 — Sur le PC Windows : activer le mode réseau

1. Ferme Atlas s'il est ouvert.
2. Dans PowerShell, dans le dossier d'Atlas :
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\windows\Mode-Reseau.ps1 -Activer
   ```
   Accepte d'approuver « Atlas CA » pour ton compte Windows (fenêtre de confirmation Windows).
3. Vérifie que ton Wi-Fi est en profil **Privé** : Paramètres → Réseau et Internet → Wi-Fi →
   (ton réseau) → Type de profil réseau : **Privé**.
4. Relance `Atlas.cmd`. Windows demande l'accès réseau pour Python : coche **Réseaux privés**
   uniquement. La fenêtre affiche l'adresse pour le Mac, par exemple `https://192.168.1.20:8765`.
5. Sur le PC, l'interface devient `https://localhost:8765`.

## Étape 2 — Copier le certificat sur le Mac

Le fichier est `donnees\certificats\atlas-ca.crt` (public, sans secret). Copie-le par clé USB,
e-mail à toi-même, ou télécharge-le depuis le Mac à l'adresse `https://192.168.1.20:8765/atlas-ca.crt`
(le navigateur affichera un avertissement la première fois : c'est normal, c'est justement ce
certificat qui va le faire disparaître).

⚠️ Ne copie **jamais** `atlas-ca.key` : c'est la clé privée, elle reste sur le PC.

## Étape 3 — Approuver le certificat sur le Mac

### Safari et Chrome (utilisent le Trousseau de macOS)
1. Double-clic sur `atlas-ca.crt` → « Trousseau d'accès » s'ouvre → ajoute-le au trousseau **session**.
2. Dans Trousseau d'accès, catégorie « Certificats », double-clic sur **Atlas CA (…)**.
3. Déplie « Se fier » → « Lors de l'utilisation de ce certificat » : **Toujours approuver**.
4. Ferme la fenêtre, saisis ton mot de passe du Mac. Quitte et relance le navigateur.

### Firefox (a sa propre liste)
Préférences → Vie privée et sécurité → Certificats → « Afficher les certificats… » → onglet
**Autorités** → « Importer… » → choisis `atlas-ca.crt` → coche « Confirmer cette AC pour identifier
des sites web » → OK.

## Étape 4 — Se connecter

1. Ouvre l'adresse affichée par Atlas (`https://192.168.1.20:8765`) : plus d'avertissement.
2. Saisis le code d'accès (`donnees\CODE-ACCES.txt` sur le PC), nomme l'appareil « Mac ».
3. Astuce : ajoute la page aux favoris. Si l'adresse IP du PC change (box Internet), Atlas
   régénère son certificat automatiquement ; regarde la nouvelle adresse dans sa fenêtre. Pour
   éviter ce changement, tu peux réserver l'adresse IP du PC dans l'interface de ta box.

## Procédure de test sur le Mac (5 minutes)

À faire une fois, et à me signaler si un point échoue :

1. **Navigateur** : note la version (Safari → À propos de Safari ; Chrome → À propos de Google Chrome).
2. **Page** : la barre d'adresse montre un cadenas, sans avertissement.
3. **Chat** : écris « Bonjour » → la réponse d'Atlas s'affiche progressivement. Sur le PC, la
   même conversation se met à jour en direct.
4. **Micro** : Réglages → Données → ligne « Navigateur » : « Contexte sécurisé : oui · Micro : oui ·
   Audio Web : oui ». Clique « Parler », autorise le micro, dis une phrase, clique « Terminer ».
   L'orbe doit réagir à ta voix pendant l'écoute.
5. **Réponse vocale** : la réponse est lue **sur le Mac** (et pas sur le PC). « Couper la voix » l'arrête.
6. **Coupure** : éteins le Wi-Fi du Mac 10 secondes → la pastille passe à « Hors ligne », puis
   revient seule. Aucun message n'est renvoyé deux fois.

## Mac ancien (macOS High Sierra 10.13)

| Navigateur | Dernière version sur 10.13 | État |
|---|---|---|
| Safari | 13.1 | prévu (audio via Web Audio, sans MediaRecorder) — **non testé** |
| Google Chrome | 116 | prévu — **non testé** sur Mac |
| Firefox ESR | 115 | prévu — **non testé** sur Mac |

L'interface est compilée pour ces versions (JavaScript ES2017, pas de fonctions CSS récentes), et
l'enregistrement du micro n'utilise pas `MediaRecorder` (absent de Safari 13). Mais je n'ai pas pu
l'essayer sur un vrai Mac : merci de faire la procédure de test ci-dessus. Si le micro ne marche
pas sur Safari 13, essaie Firefox ESR 115 ou Chrome 116.

Atlas n'utilise **pas** la reconnaissance vocale intégrée du navigateur (qui envoie l'audio à
Google ou Apple) : ta voix part uniquement vers ton PC Windows.

## Revenir en accès local seulement

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\Mode-Reseau.ps1 -Desactiver
```
Puis relance Atlas. Pour supprimer la confiance sur le Mac : Trousseau d'accès → supprimer « Atlas CA ».
