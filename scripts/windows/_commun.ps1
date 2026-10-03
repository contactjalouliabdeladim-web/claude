# Fonctions communes aux scripts Atlas (Windows PowerShell 5.1 et PowerShell 7).
# Ce fichier est chargé par les autres scripts ; il ne fait rien seul.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$Racine = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Backend = Join-Path $Racine 'backend'
$Donnees = if ($env:ATLAS_DATA_DIR) { $env:ATLAS_DATA_DIR } else { Join-Path $Racine 'donnees' }
$VenvPython = Join-Path $Racine '.venv\Scripts\python.exe'
$FichierConfig = Join-Path $Donnees 'atlas.env'

function Ecrire-Etape([string]$Texte) {
    Write-Host ''
    Write-Host "==> $Texte" -ForegroundColor Yellow
}

function Ecrire-Ok([string]$Texte) { Write-Host "    OK  $Texte" -ForegroundColor Green }
function Ecrire-Info([string]$Texte) { Write-Host "    $Texte" }
function Ecrire-Alerte([string]$Texte) { Write-Host "    ATTENTION : $Texte" -ForegroundColor Magenta }

function Arreter-AvecErreur([string]$Texte) {
    Write-Host ''
    Write-Host "ERREUR : $Texte" -ForegroundColor Red
    Write-Host ''
    exit 1
}

function Demander-OuiNon([string]$Question, [bool]$ParDefaut = $true) {
    $suffixe = if ($ParDefaut) { '[O/n]' } else { '[o/N]' }
    $reponse = Read-Host "$Question $suffixe"
    if ([string]::IsNullOrWhiteSpace($reponse)) { return $ParDefaut }
    return $reponse.Trim().ToLower().StartsWith('o') -or $reponse.Trim().ToLower().StartsWith('y')
}

function Lire-Env([string]$Chemin) {
    $valeurs = @{}
    if (-not (Test-Path -LiteralPath $Chemin)) { return $valeurs }
    foreach ($ligne in Get-Content -LiteralPath $Chemin -Encoding UTF8) {
        $l = $ligne.Trim()
        if ($l -eq '' -or $l.StartsWith('#') -or -not $l.Contains('=')) { continue }
        $i = $l.IndexOf('=')
        $cle = $l.Substring(0, $i).Trim()
        $val = $l.Substring($i + 1).Trim().Trim('"').Trim("'")
        $valeurs[$cle] = $val
    }
    return $valeurs
}

function Ecrire-ValeurEnv([string]$Chemin, [string]$Cle, [string]$Valeur) {
    $lignes = @()
    if (Test-Path -LiteralPath $Chemin) { $lignes = @(Get-Content -LiteralPath $Chemin -Encoding UTF8) }
    $trouve = $false
    $resultat = foreach ($ligne in $lignes) {
        if ($ligne -match "^\s*$([regex]::Escape($Cle))\s*=") { $trouve = $true; "$Cle=$Valeur" } else { $ligne }
    }
    if ($null -eq $resultat) { $resultat = @() }
    if (-not $trouve) { $resultat = @($resultat) + "$Cle=$Valeur" }
    $utf8SansBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Chemin, [string[]]$resultat, $utf8SansBom)
}

function Verifier-Installation {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Arreter-AvecErreur "Atlas n'est pas encore installé. Double-clique d'abord sur « Installer-Atlas.cmd »."
    }
}

function Executer-Atlas([string[]]$Arguments) {
    # Exécute « python -m atlas_server ... » depuis le dossier backend.
    Push-Location -LiteralPath $Backend
    try {
        & $VenvPython -m atlas_server @Arguments | Out-Host
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

function Obtenir-AdresseLocale {
    $cfg = Lire-Env $FichierConfig
    $port = if ($cfg['ATLAS_PORT']) { $cfg['ATLAS_PORT'] } else { '8765' }
    $reseau = $cfg['ATLAS_LAN'] -eq '1'
    $schema = if ($reseau) { 'https' } else { 'http' }
    return @{ Url = "${schema}://localhost:$port"; Port = [int]$port; Reseau = $reseau }
}

function Tester-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $tache = $client.ConnectAsync('127.0.0.1', $Port)
        return $tache.Wait(400) -and $client.Connected
    } catch { return $false } finally { $client.Close() }
}

function Dossier-Hermes {
    if ($env:HERMES_HOME) { return $env:HERMES_HOME }
    return (Join-Path $env:LOCALAPPDATA 'hermes')
}
