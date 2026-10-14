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
# Un clic dans cette fenêtre activait le mode « Sélection » de Windows, qui fige Atlas (plus de réponse,
# plus d'appels) jusqu'à un appui sur Échap : on désactive ce mode pour cette fenêtre.
try {
    Add-Type -Namespace AtlasConsole -Name Mode -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int h);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);
'@
    $entree = [AtlasConsole.Mode]::GetStdHandle(-10)
    $mode = 0
    if ([AtlasConsole.Mode]::GetConsoleMode($entree, [ref]$mode)) {
        [void][AtlasConsole.Mode]::SetConsoleMode($entree, ($mode -band (-bnot 0x40)) -bor 0x80)
    }
} catch { }
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
