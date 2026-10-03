<#
  Installation guidée d'Atlas sur Windows (sans droits administrateur).
  - Crée un environnement Python isolé dans le dossier du projet (.venv) ;
  - installe les dépendances figées (backend\requirements.txt) ;
  - prépare le dossier « donnees » (configuration, base, code d'accès) ;
  - avec ton accord, active le serveur API officiel d'Hermes (sauvegarde préalable de sa configuration).
  Relançable sans risque : ce qui existe déjà est conservé.
#>
param(
    [switch]$VoixLocale,
    [switch]$SansVoixLocale,
    [switch]$NonInteractif
)
. (Join-Path $PSScriptRoot '_commun.ps1')

Write-Host ''
Write-Host '  Installation d''Atlas' -ForegroundColor Yellow
Write-Host "  Dossier : $Racine"

# --- 1. Python ----------------------------------------------------------------------------
Ecrire-Etape '1/6  Recherche de Python 3.11 à 3.13'
$pythonBase = $null
$candidats = @(
    @('py', '-3.12'), @('py', '-3.11'), @('py', '-3.13'), @('python', $null), @('python3', $null)
)
foreach ($c in $candidats) {
    $cmd = Get-Command $c[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $argsVersion = @()
    if ($c[1]) { $argsVersion += $c[1] }
    $argsVersion += @('-c', 'import sys; print("%d.%d" % sys.version_info[:2])')
    try {
        $version = (& $c[0] @argsVersion 2>$null | Select-Object -First 1)
    } catch { continue }
    if ($version -match '^3\.(11|12|13)$') {
        $pythonBase = $c
        Ecrire-Ok "Python $version trouvé ($($c[0]) $($c[1]))"
        break
    }
}
if (-not $pythonBase) {
    Write-Host ''
    Write-Host '  Python 3.12 n''est pas installé. Installe-le (sans droits administrateur) avec :' -ForegroundColor Yellow
    Write-Host '      winget install -e --id Python.Python.3.12 --scope user'
    Write-Host '  ou depuis https://www.python.org/downloads/ (case « Add python.exe to PATH »),'
    Write-Host '  puis ferme cette fenêtre et relance Installer-Atlas.cmd.'
    Arreter-AvecErreur 'Python 3.11, 3.12 ou 3.13 est nécessaire.'
}

# --- 2. Environnement isolé ----------------------------------------------------------------
Ecrire-Etape '2/6  Environnement Python isolé (.venv)'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    $argsVenv = @()
    if ($pythonBase[1]) { $argsVenv += $pythonBase[1] }
    $argsVenv += @('-m', 'venv', (Join-Path $Racine '.venv'))
    & $pythonBase[0] @argsVenv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) { Arreter-AvecErreur 'Création de .venv impossible.' }
    Ecrire-Ok '.venv créé'
} else {
    Ecrire-Ok '.venv déjà présent'
}

# --- 3. Dépendances -------------------------------------------------------------------------
Ecrire-Etape '3/6  Installation des dépendances (quelques minutes la première fois)'
& $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Backend 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Arreter-AvecErreur 'Installation des dépendances échouée (connexion Internet ?).' }
Ecrire-Ok 'Dépendances installées'

$installerVoix = $VoixLocale
if (-not $VoixLocale -and -not $SansVoixLocale -and -not $NonInteractif) {
    Ecrire-Info 'La transcription vocale locale (faster-whisper) garde ta voix sur ce PC.'
    Ecrire-Info 'Elle demande ~300 Mo, puis ~500 Mo de modèle téléchargé à la première dictée.'
    $installerVoix = Demander-OuiNon '    Installer la transcription vocale locale ?' $true
}
if ($installerVoix) {
    & $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Backend 'requirements-voix-locale.txt')
    if ($LASTEXITCODE -ne 0) { Ecrire-Alerte 'Transcription locale non installée (tu pourras relancer l''installation).' }
    else { Ecrire-Ok 'Transcription vocale locale installée' }
}

# --- 4. Données ---------------------------------------------------------------------------------
Ecrire-Etape '4/6  Préparation du dossier « donnees »'
$code = Executer-Atlas @('init')
if ($code -ne 0) { Arreter-AvecErreur 'Préparation des données impossible.' }
$cfg = Lire-Env $FichierConfig
if ($installerVoix -eq $false -and $cfg['ATLAS_STT'] -eq 'local_whisper') {
    & $VenvPython -c "import faster_whisper" 2>$null
    if ($LASTEXITCODE -ne 0) { Ecrire-Info 'Dictée : non disponible tant que la transcription n''est pas installée.' }
}

# --- 5. Hermes -----------------------------------------------------------------------------------
Ecrire-Etape '5/6  Connexion au serveur API officiel d''Hermes'
$hermesHome = Dossier-Hermes
$hermesEnv = Join-Path $hermesHome '.env'
$hermesCmd = Get-Command hermes -ErrorAction SilentlyContinue
Ecrire-Info "Dossier Hermes : $hermesHome"
if (-not (Test-Path -LiteralPath $hermesHome)) {
    Ecrire-Alerte 'Dossier Hermes introuvable. Si Hermes est installé ailleurs, définis HERMES_HOME puis relance.'
} else {
    $hermesValeurs = Lire-Env $hermesEnv
    $cleHermes = $hermesValeurs['API_SERVER_KEY']
    $actif = ($hermesValeurs['API_SERVER_ENABLED'] -match '^(?i)(true|1|yes|on)$')
    if ($cleHermes -and $cleHermes.Length -ge 16) {
        # Hermes a déjà une clé : Atlas la réutilise, Hermes n'est pas modifié.
        Ecrire-ValeurEnv $FichierConfig 'HERMES_API_KEY' $cleHermes
        Ecrire-Ok 'Clé API Hermes existante réutilisée par Atlas (non affichée).'
        if (-not $actif) { Ecrire-Alerte 'API_SERVER_ENABLED n''est pas à true dans la configuration d''Hermes.' }
    }
    if (-not ($cleHermes -and $cleHermes.Length -ge 16) -or -not $actif) {
        Ecrire-Info 'Atlas a besoin du serveur API d''Hermes (désactivé par défaut, écoute uniquement sur ce PC).'
        Ecrire-Info 'Modification prévue dans la configuration d''Hermes : API_SERVER_ENABLED=true et API_SERVER_KEY=<clé>.'
        Ecrire-Info 'Rien d''autre n''est changé (ni modèle, ni fournisseur, ni mémoire, ni personnalité).'
        $ok = $false
        if (-not $NonInteractif) { $ok = Demander-OuiNon '    Activer le serveur API d''Hermes maintenant (avec sauvegarde) ?' $true }
        if ($ok) {
            if (Test-Path -LiteralPath $hermesEnv) {
                $sauvegarde = "$hermesEnv.avant-atlas-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
                Copy-Item -LiteralPath $hermesEnv -Destination $sauvegarde
                Ecrire-Ok "Sauvegarde : $sauvegarde"
            }
            $cfg = Lire-Env $FichierConfig
            $cle = if ($cleHermes -and $cleHermes.Length -ge 16) { $cleHermes } else { $cfg['HERMES_API_KEY'] }
            $faitParHermes = $false
            if ($hermesCmd) {
                & hermes config set API_SERVER_ENABLED true | Out-Null
                $r1 = $LASTEXITCODE
                & hermes config set API_SERVER_KEY $cle | Out-Null
                $faitParHermes = ($r1 -eq 0 -and $LASTEXITCODE -eq 0)
            }
            if (-not $faitParHermes) {
                Ecrire-Info 'Commande « hermes config set » indisponible : écriture directe dans le fichier .env d''Hermes.'
                Ecrire-ValeurEnv $hermesEnv 'API_SERVER_ENABLED' 'true'
                Ecrire-ValeurEnv $hermesEnv 'API_SERVER_KEY' $cle
            }
            Ecrire-ValeurEnv $FichierConfig 'HERMES_API_KEY' $cle
            Ecrire-Ok 'Serveur API d''Hermes activé (clé partagée avec Atlas, non affichée).'
            Ecrire-Alerte 'Il faut redémarrer la passerelle Hermes pour en tenir compte.'
            if ($hermesCmd -and (Demander-OuiNon '    Redémarrer la passerelle Hermes maintenant (hermes gateway restart) ?' $true)) {
                & hermes gateway restart
                Start-Sleep -Seconds 8
            } else {
                Ecrire-Info 'À faire plus tard dans PowerShell :  hermes gateway restart'
            }
        } else {
            Ecrire-Info 'Pas de modification. Voir docs\DEMARRAGE-WINDOWS.md, section « Activer le serveur API d''Hermes ».'
        }
    }
}

# --- 6. Vérification --------------------------------------------------------------------------
Ecrire-Etape '6/6  Vérification (aucun appel au modèle)'
$null = Executer-Atlas @('verifier')
if ($LASTEXITCODE -ne 0) {
    Ecrire-Alerte 'Hermes n''est pas encore joignable. Lance « hermes gateway » (ou « hermes gateway start ») puis relance Atlas.'
}

$fichierCode = Join-Path $Donnees 'CODE-ACCES.txt'
Write-Host ''
Write-Host '  Installation terminée.' -ForegroundColor Green
if (Test-Path -LiteralPath $fichierCode) { Write-Host ('  ' + ((Get-Content -LiteralPath $fichierCode -Encoding UTF8) -join ' ').Trim()) }
Write-Host '  Pour démarrer : double-clic sur « Atlas.cmd » à la racine du dossier.'
Write-Host ''
