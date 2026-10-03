<#
  Active ou désactive l'accès depuis le réseau local (Mac…), en HTTPS avec un certificat local.
    Mode-Reseau.ps1 -Activer
    Mode-Reseau.ps1 -Desactiver
  Aucune ouverture vers Internet, aucune redirection de port.
#>
param([switch]$Activer, [switch]$Desactiver)
. (Join-Path $PSScriptRoot '_commun.ps1')
Verifier-Installation
if (-not (Test-Path -LiteralPath $FichierConfig)) { $null = Executer-Atlas @('init') }

if ($Desactiver) {
    Ecrire-ValeurEnv $FichierConfig 'ATLAS_LAN' '0'
    Ecrire-Ok 'Mode réseau désactivé : Atlas n''est accessible que depuis ce PC (http://localhost). Relance Atlas.'
    exit 0
}
if (-not $Activer) {
    Write-Host 'Usage : Mode-Reseau.ps1 -Activer   ou   Mode-Reseau.ps1 -Desactiver'
    exit 1
}

Ecrire-Etape 'Génération des certificats HTTPS locaux'
$null = Executer-Atlas @('certificats')
if ($LASTEXITCODE -ne 0) { Arreter-AvecErreur 'Génération des certificats impossible.' }
Ecrire-ValeurEnv $FichierConfig 'ATLAS_LAN' '1'
Ecrire-Ok 'Mode réseau activé.'

$ca = Join-Path $Donnees 'certificats\atlas-ca.crt'
Ecrire-Etape 'Confiance du certificat sur CE PC (pour https://localhost sans avertissement)'
Ecrire-Info 'Windows va afficher une fenêtre de confirmation : vérifie que le nom commence par « Atlas CA ».'
if (Demander-OuiNon '    Ajouter « Atlas CA » aux autorités de confiance de ton compte Windows ?' $true) {
    try {
        Import-Certificate -FilePath $ca -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null
        Ecrire-Ok 'Certificat approuvé pour ton compte (sans droits administrateur).'
    } catch {
        Ecrire-Alerte "Import refusé : $($_.Exception.Message)"
    }
}

Write-Host ''
Write-Host '  Étapes suivantes :' -ForegroundColor Yellow
Write-Host '  1. Relance Atlas (Atlas.cmd). Au premier lancement, Windows demande l''autorisation réseau pour Python :'
Write-Host '     coche UNIQUEMENT « Réseaux privés » (pas « publics »).'
Write-Host "  2. Copie sur le Mac le fichier : $ca"
Write-Host '     puis suis docs\ACCES-MAC.md pour l''approuver dans le Trousseau.'
Write-Host '  3. Sur le Mac, ouvre l''une des adresses affichées par Atlas au démarrage (https://…:8765).'
