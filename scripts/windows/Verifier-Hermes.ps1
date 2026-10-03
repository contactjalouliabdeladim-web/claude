<#
  Diagnostic en lecture seule de l'installation Hermes et de la connexion Atlas ↔ Hermes.
  Aucun appel au modèle, aucune modification. Affiche ce qu'il faut vérifier.
#>
. (Join-Path $PSScriptRoot '_commun.ps1')

Ecrire-Etape 'Commande hermes'
$hermesCmd = Get-Command hermes -ErrorAction SilentlyContinue
if ($hermesCmd) {
    Ecrire-Ok "Trouvée : $($hermesCmd.Source)"
    & hermes --version 2>&1 | Select-Object -First 3 | ForEach-Object { Ecrire-Info $_ }
} else {
    Ecrire-Alerte 'Commande « hermes » introuvable dans ce terminal (ouvre un nouveau PowerShell).'
}

Ecrire-Etape 'Dossier de données Hermes'
$hermesHome = Dossier-Hermes
Ecrire-Info $hermesHome
foreach ($f in @('config.yaml', '.env', 'SOUL.md', 'memories\MEMORY.md', 'skills')) {
    $p = Join-Path $hermesHome $f
    if (Test-Path -LiteralPath $p) { Ecrire-Ok "$f présent" } else { Ecrire-Info "$f absent" }
}
$hermesValeurs = Lire-Env (Join-Path $hermesHome '.env')
$etat = if ($hermesValeurs['API_SERVER_ENABLED']) { $hermesValeurs['API_SERVER_ENABLED'] } else { '(non défini)' }
Ecrire-Info "API_SERVER_ENABLED = $etat"
Ecrire-Info ("API_SERVER_KEY = " + $(if ($hermesValeurs['API_SERVER_KEY']) { '(définie, non affichée)' } else { '(absente)' }))
$cfgYaml = Join-Path $hermesHome 'config.yaml'
if (Test-Path -LiteralPath $cfgYaml) {
    Ecrire-Etape 'Modèle configuré (lecture de config.yaml, sans secret)'
    Select-String -LiteralPath $cfgYaml -Pattern '^\s*(provider|default|model|voice|reasoning_effort)\s*:' |
        Select-Object -First 12 | ForEach-Object { Ecrire-Info $_.Line.Trim() }
}

Ecrire-Etape 'Passerelle Hermes'
if ($hermesCmd) { & hermes gateway status 2>&1 | Select-Object -First 8 | ForEach-Object { Ecrire-Info $_ } }

Ecrire-Etape 'Connexion Atlas → Hermes'
if (Test-Path -LiteralPath $VenvPython) { $null = Executer-Atlas @('verifier') } else { Ecrire-Info 'Atlas non installé.' }
