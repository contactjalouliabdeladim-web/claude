<#
  Démarre Atlas et ouvre l'interface dans le navigateur.
  La fenêtre doit rester ouverte : la fermer (ou Ctrl+C) arrête Atlas.
  -SansNavigateur : ne pas ouvrir le navigateur (utile au démarrage automatique).
#>
param([switch]$SansNavigateur)
. (Join-Path $PSScriptRoot '_commun.ps1')
Verifier-Installation

$adresse = Obtenir-AdresseLocale
if (Tester-Port $adresse.Port) {
    Ecrire-Info "Atlas semble déjà lancé sur le port $($adresse.Port)."
    if (-not $SansNavigateur) { Start-Process $adresse.Url }
    exit 0
}

$host.UI.RawUI.WindowTitle = 'Atlas — serveur (ne pas fermer)'
Write-Host ''
Write-Host '  Atlas démarre…  (laisser cette fenêtre ouverte ; Ctrl+C pour arrêter)' -ForegroundColor Yellow
$processus = Start-Process -FilePath $VenvPython -ArgumentList @('-m', 'atlas_server', 'serve') `
    -WorkingDirectory $Backend -NoNewWindow -PassThru

$pret = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if ($processus.HasExited) { break }
    if (Tester-Port $adresse.Port) { $pret = $true; break }
}
if (-not $pret) {
    if ($processus.HasExited) { Arreter-AvecErreur "Atlas s'est arrêté au démarrage (code $($processus.ExitCode)). Lis les messages ci-dessus." }
    Arreter-AvecErreur 'Atlas ne répond pas après 30 secondes.'
}
if (-not $SansNavigateur) { Start-Process $adresse.Url }
Write-Host "  Interface : $($adresse.Url)" -ForegroundColor Green
try {
    $processus.WaitForExit()
} finally {
    if (-not $processus.HasExited) { Stop-Process -Id $processus.Id -ErrorAction SilentlyContinue }
}
