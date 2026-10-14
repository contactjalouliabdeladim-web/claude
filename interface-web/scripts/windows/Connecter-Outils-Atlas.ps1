<#
  Donne à Hermes les outils d'Atlas (tâches, priorités, notes) : quand tu demandes à Atlas
  « ajoute une tâche… », elle apparaît dans l'interface.
  Technique : Hermes lance lui-même un petit serveur d'outils MCP d'Atlas (connexion « stdio »),
  qui écrit dans la base d'Atlas et prévient l'interface. Fonctionne quel que soit l'ordre de démarrage.

  Modifications dans Hermes (après sauvegarde de config.yaml) :
    mcp_servers.atlas.command = <dossier Atlas>\.venv\Scripts\python.exe
    mcp_servers.atlas.args    = [-m, atlas_server, mcp-stdio]
    mcp_servers.atlas.cwd     = <dossier Atlas>\backend
  Rien d'autre n'est modifié. Pour annuler : hermes mcp remove atlas
#>
param([switch]$NonInteractif)
. (Join-Path $PSScriptRoot '_commun.ps1')
Verifier-Installation

$hermesCmd = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermesCmd) { Arreter-AvecErreur 'Commande « hermes » introuvable. Ouvre un nouveau PowerShell et réessaie.' }

# Le jeton de la liaison interne est créé par Atlas s'il manque (configuration d'une version précédente).
$null = Executer-Atlas @('init')

Ecrire-Etape 'Outils d''Atlas pour Hermes'
Ecrire-Info 'Hermes pourra lire et modifier tes tâches, priorités et notes dans Atlas.'
Ecrire-Info 'Il ne s''en sert que quand tu le demandes ; chaque action apparaît dans le panneau d''activité.'
if (-not $NonInteractif) {
    if (-not (Demander-OuiNon '    Connecter les outils d''Atlas à Hermes (avec sauvegarde) ?' $true)) { exit 0 }
}

$hermesHome = Dossier-Hermes
$cfgYaml = Join-Path $hermesHome 'config.yaml'
if (Test-Path -LiteralPath $cfgYaml) {
    $sauvegarde = "$cfgYaml.avant-atlas-outils-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $cfgYaml -Destination $sauvegarde
    Ecrire-Ok "Sauvegarde : $sauvegarde"
}

# Pas de guillemets doubles dans les valeurs : PowerShell 5.1 les retire des arguments.
& hermes config set mcp_servers.atlas.command $VenvPython | Out-Null
$r1 = $LASTEXITCODE
& hermes config set mcp_servers.atlas.args '[-m, atlas_server, mcp-stdio]' | Out-Null
$r2 = $LASTEXITCODE
& hermes config set mcp_servers.atlas.cwd $Backend | Out-Null
$r3 = $LASTEXITCODE
if ($r1 -ne 0 -or $r2 -ne 0 -or $r3 -ne 0) {
    Arreter-AvecErreur 'hermes config set a échoué. Restaure la sauvegarde si besoin et vérifie « hermes --version ».'
}
Ecrire-Ok 'Outils d''Atlas déclarés dans Hermes.'

Ecrire-Etape 'Test de la connexion (sans appel au modèle)'
$ErrorActionPreference = 'Continue'
& hermes mcp test atlas 2>&1 | Select-Object -First 16 | ForEach-Object { Ecrire-Info $_ }
$ErrorActionPreference = 'Stop'

Ecrire-Alerte 'Redémarre la passerelle Hermes pour qu''elle charge les nouveaux outils.'
if (-not $NonInteractif -and (Demander-OuiNon '    Redémarrer la passerelle Hermes maintenant (hermes gateway restart) ?' $true)) {
    & hermes gateway restart
}
