# Arrête le serveur Atlas lancé sur ce PC (n'arrête PAS Hermes).
. (Join-Path $PSScriptRoot '_commun.ps1')
$fichierPid = Join-Path $Donnees 'atlas.pid'
if (-not (Test-Path -LiteralPath $fichierPid)) {
    Ecrire-Info 'Atlas ne semble pas lancé (aucun fichier atlas.pid).'
    exit 0
}
$id = [int](Get-Content -LiteralPath $fichierPid -Raw).Trim()
$proc = Get-Process -Id $id -ErrorAction SilentlyContinue
if ($proc -and $proc.ProcessName -like 'python*') {
    Stop-Process -Id $id
    Ecrire-Ok "Atlas arrêté (processus $id)."
} else {
    Ecrire-Info 'Le processus indiqué n''existe plus.'
}
Remove-Item -LiteralPath $fichierPid -ErrorAction SilentlyContinue
