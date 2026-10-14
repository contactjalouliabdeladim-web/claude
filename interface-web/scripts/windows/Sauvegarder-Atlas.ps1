# Sauvegarde la base d'Atlas (conversations, notes, priorités, réglages) + un export lisible.
# La copie est cohérente même si Atlas est en cours d'utilisation.
. (Join-Path $PSScriptRoot '_commun.ps1')
Verifier-Installation
$null = Executer-Atlas @('sauvegarder')
if ($LASTEXITCODE -ne 0) { Arreter-AvecErreur 'Sauvegarde impossible.' }
$dossier = Join-Path $Donnees 'sauvegardes'
Ecrire-Info 'Copie aussi ce dossier sur une clé USB ou un disque externe de temps en temps.'
Ecrire-Info 'Les fichiers d''Hermes (mémoire, personnalité) se sauvegardent séparément : dossier %LOCALAPPDATA%\hermes.'
Start-Process explorer.exe -ArgumentList $dossier
