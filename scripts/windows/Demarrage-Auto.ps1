<#
  Option (désactivée par défaut) : lancer Atlas à l'ouverture de session Windows.
    Demarrage-Auto.ps1 -Activer      crée un raccourci dans le dossier Démarrage de ton compte
    Demarrage-Auto.ps1 -Desactiver   le supprime
  Pas de droits administrateur. Hermes (hermes gateway) doit lui aussi être lancé : voir « hermes gateway install ».
#>
param([switch]$Activer, [switch]$Desactiver)
. (Join-Path $PSScriptRoot '_commun.ps1')
$dossierDemarrage = [Environment]::GetFolderPath('Startup')
$raccourci = Join-Path $dossierDemarrage 'Atlas.lnk'

if ($Desactiver) {
    Remove-Item -LiteralPath $raccourci -ErrorAction SilentlyContinue
    Ecrire-Ok 'Démarrage automatique désactivé.'
    exit 0
}
if (-not $Activer) {
    Write-Host 'Usage : Demarrage-Auto.ps1 -Activer   ou   Demarrage-Auto.ps1 -Desactiver'
    exit 1
}
Verifier-Installation
$shell = New-Object -ComObject WScript.Shell
$lien = $shell.CreateShortcut($raccourci)
$lien.TargetPath = 'powershell.exe'
$lien.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$(Join-Path $PSScriptRoot 'Lancer-Atlas.ps1')`" -SansNavigateur"
$lien.WorkingDirectory = $Racine
$lien.WindowStyle = 7
$lien.Description = 'Démarre le serveur Atlas'
$lien.Save()
Ecrire-Ok "Démarrage automatique activé : $raccourci"
