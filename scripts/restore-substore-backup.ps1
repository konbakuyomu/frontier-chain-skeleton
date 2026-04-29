<#
.SYNOPSIS
  List or restore VPS Sub-Store JSON backups.

.DESCRIPTION
  Default mode lists recent backup filenames only. To restore, pass both
  -BackupName and -Apply. Restore creates a pre-restore backup of the current
  data file, copies the selected backup over sub-store.json, and restarts the
  Sub-Store container.

.EXAMPLE
  .\scripts\restore-substore-backup.ps1 -SshHost <vps-host> -SshPort <ssh-port> -SshKey <key>

.EXAMPLE
  .\scripts\restore-substore-backup.ps1 -BackupName sub-store.json.bak-deploy-20260429-230000 -Apply -SshHost <vps-host> -SshPort <ssh-port> -SshKey <key>
#>

[CmdletBinding()]
param(
  [switch]$Apply,
  [string]$BackupName,

  [string]$SshHost = $env:FRONTIER_SUBSTORE_SSH_HOST,
  [string]$SshPort = $env:FRONTIER_SUBSTORE_SSH_PORT,
  [string]$SshUser = $env:FRONTIER_SUBSTORE_SSH_USER,
  [string]$SshKey = $env:FRONTIER_SUBSTORE_SSH_KEY,

  [string]$SubStoreDir = '/opt/1panel/apps/sub-store/sub-store',
  [string]$ContainerName = 'sub-store',
  [int]$ListCount = 20
)

$ErrorActionPreference = 'Stop'

if (-not $SshPort) { $SshPort = '22' }
if (-not $SshUser) { $SshUser = 'root' }
if (-not $SshHost) { throw 'FRONTIER_SUBSTORE_SSH_HOST or -SshHost is required' }

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

function Get-SshArgs {
  $args = @()
  if ($SshKey) { $args += @('-i', $SshKey) }
  $args += @('-p', $SshPort, '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new', "$SshUser@$SshHost")
  return $args
}

function Quote-Remote($Text) {
  return "'" + $Text.Replace("'", "'\''") + "'"
}

if ($Apply -and -not $BackupName) {
  throw '-Apply requires -BackupName'
}

if ($BackupName -and $BackupName -notmatch '^sub-store\.json\.[A-Za-z0-9_.-]+$') {
  throw 'BackupName must be a plain backup filename under backups/, for example sub-store.json.bak-deploy-YYYYMMDD-HHMMSS'
}

$sshArgs = Get-SshArgs

if (-not $Apply) {
  Write-Info "listing recent Sub-Store backups on VPS"
  $cmd = @"
set -euo pipefail
cd $(Quote-Remote $SubStoreDir)
python3 - <<'PY'
from pathlib import Path
items=[]
for p in Path('backups').glob('sub-store.json.*'):
    st=p.stat()
    items.append((st.st_mtime, p.name, st.st_size))
for _, name, size in sorted(items, reverse=True)[:$ListCount]:
    print(f'{name}\t{size} bytes')
PY
"@
  & ssh @sshArgs $cmd
  if ($LASTEXITCODE -ne 0) { throw 'remote backup listing failed' }
  Write-Warn2 'No restore performed. Add -Apply -BackupName <name> to restore.'
  exit 0
}

Write-Info "restoring Sub-Store backup: $BackupName"
$restore = @"
set -euo pipefail
cd $(Quote-Remote $SubStoreDir)
BACKUP_NAME=$(Quote-Remote $BackupName)
SRC="backups/\$BACKUP_NAME"
if [ ! -f "\$SRC" ]; then
  echo "backup not found: \$BACKUP_NAME" >&2
  exit 1
fi
python3 -m json.tool "\$SRC" >/dev/null
python3 -m json.tool data/sub-store.json >/dev/null
STAMP=\$(date +%Y%m%d-%H%M%S)
cp -p data/sub-store.json "backups/sub-store.json.bak-before-restore-\$STAMP"
cp -p "\$SRC" data/sub-store.json
docker restart $(Quote-Remote $ContainerName) >/dev/null
echo "restored \$BACKUP_NAME"
echo "pre_restore_backup=sub-store.json.bak-before-restore-\$STAMP"
"@

& ssh @sshArgs $restore
if ($LASTEXITCODE -ne 0) { throw 'remote restore failed' }
Write-Ok 'restore completed'
