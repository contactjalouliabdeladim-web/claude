# Met à jour Atlas : récupère la dernière version (si le dossier vient de git) et les dépendances.
# Arrête Atlas avant (fermer sa fenêtre). Les données (dossier « donnees ») ne sont pas touchées.
. (Join-Path $PSScriptRoot '_commun.ps1')
Verifier-Installation

Ecrire-Etape '1/3  Sauvegarde préalable'
$null = Executer-Atlas @('sauvegarder')

Ecrire-Etape '2/3  Nouvelle version du code'
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git -and (Test-Path -LiteralPath (Join-Path $Racine '.git'))) {
    Push-Location -LiteralPath $Racine
    try {
        & git pull --ff-only
        if ($LASTEXITCODE -ne 0) { Arreter-AvecErreur 'git pull a échoué (modifications locales ?). Rien n''a été changé.' }
    } finally { Pop-Location }
} else {
    Ecrire-Info 'Dossier non géré par git : remplace les fichiers par la nouvelle version (garde « donnees » et « .venv »).'
}

Ecrire-Etape '3/3  Dépendances'
& $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Backend 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Arreter-AvecErreur 'Mise à jour des dépendances échouée.' }
# PowerShell 5.1 : une sortie d'erreur redirigée devient une erreur bloquante sous « Stop ».
$ErrorActionPreference = 'Continue'
& $VenvPython -c "import faster_whisper" 2>$null
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -eq 0) {
    & $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Backend 'requirements-voix-locale.txt')
}
Ecrire-Ok 'Atlas est à jour. Relance-le avec Atlas.cmd (le schéma de la base évolue automatiquement).'
