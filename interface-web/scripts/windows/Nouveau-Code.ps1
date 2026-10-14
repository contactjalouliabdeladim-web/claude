# Génère un nouveau code d'accès. Tous les appareils (Windows, Mac) devront se reconnecter.
. (Join-Path $PSScriptRoot '_commun.ps1')
Verifier-Installation
$null = Executer-Atlas @('nouveau-code')
Ecrire-Info 'Si Atlas est lancé, le nouveau code est actif immédiatement.'
