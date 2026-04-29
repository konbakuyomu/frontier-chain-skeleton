<#
.SYNOPSIS
  Verify the VPS Sub-Store subscription center without printing secrets.

.DESCRIPTION
  Runs local syntax checks for repository scripts, then optionally connects to
  the VPS and executes read-only checks against the live Sub-Store data file and
  local HTTP endpoints. The remote verifier prints counts and booleans only.

.EXAMPLE
  .\scripts\verify-substore.ps1

.EXAMPLE
  .\scripts\verify-substore.ps1 -SkipRemote
#>

[CmdletBinding()]
param(
  [switch]$SkipRemote,
  [switch]$SkipHttp,

  [string]$SshHost = $env:FRONTIER_SUBSTORE_SSH_HOST,
  [string]$SshPort = $env:FRONTIER_SUBSTORE_SSH_PORT,
  [string]$SshUser = $env:FRONTIER_SUBSTORE_SSH_USER,
  [string]$SshKey = $env:FRONTIER_SUBSTORE_SSH_KEY,

  [string]$SubStoreDir = '/opt/1panel/apps/sub-store/sub-store',
  [string]$ContainerName = 'sub-store'
)

$ErrorActionPreference = 'Stop'

if (-not $SshPort) { $SshPort = '22' }
if (-not $SshUser) { $SshUser = 'root' }

$RepoRoot = Split-Path -Parent $PSScriptRoot
$JsFiles = @(
  (Join-Path $RepoRoot 'substore-source-marker.js'),
  (Join-Path $RepoRoot 'shadowrocket-nodes-injector.js'),
  (Join-Path $RepoRoot 'main.js')
)
$PyFiles = @(
  (Join-Path $PSScriptRoot 'remote-verify-substore.py'),
  (Join-Path $PSScriptRoot 'remote-apply-substore.py'),
  (Join-Path $PSScriptRoot 'update-powerfullz-inline.py')
)

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

function Get-SshArgs {
  $args = @()
  if ($SshKey) { $args += @('-i', $SshKey) }
  $args += @('-p', $SshPort, '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new', "$SshUser@$SshHost")
  return $args
}

function Get-ScpArgs {
  $args = @()
  if ($SshKey) { $args += @('-i', $SshKey) }
  $args += @('-P', $SshPort, '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new')
  return $args
}

function Quote-Remote($Text) {
  return "'" + $Text.Replace("'", "'\''") + "'"
}

Write-Info "repo root: $RepoRoot"

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
  foreach ($file in $JsFiles) {
    & node --check $file
    if ($LASTEXITCODE -ne 0) { throw "node --check failed: $file" }
    Write-Ok "node --check passed: $(Split-Path -Leaf $file)"
  }
} else {
  Write-Warn2 'node not found, skipped JS syntax checks'
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
  & python -B -c "import pathlib,sys; [compile(pathlib.Path(p).read_text(encoding='utf-8'), p, 'exec') for p in sys.argv[1:]]" @PyFiles
  if ($LASTEXITCODE -ne 0) { throw 'python syntax check failed' }
  Write-Ok 'python syntax checks passed'
} else {
  Write-Warn2 'python not found locally, skipped Python syntax checks'
}

if ($SkipRemote) {
  Write-Warn2 'remote checks skipped by -SkipRemote'
  exit 0
}

if (-not $SshHost) {
  Write-Warn2 'remote checks skipped; set FRONTIER_SUBSTORE_SSH_HOST or pass -SshHost'
  exit 0
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$remoteStage = "/tmp/frontier-substore-verify-$runId"
$sshArgs = Get-SshArgs
$scpArgs = Get-ScpArgs

& ssh @sshArgs ("mkdir -p " + (Quote-Remote $remoteStage))
if ($LASTEXITCODE -ne 0) { throw 'ssh mkdir failed' }

try {
  $remoteScript = "${SshUser}@${SshHost}:$remoteStage/remote-verify-substore.py"
  & scp @scpArgs (Join-Path $PSScriptRoot 'remote-verify-substore.py') $remoteScript
  if ($LASTEXITCODE -ne 0) { throw 'scp verify script failed' }

  $cmd = @(
    'python3',
    (Quote-Remote "$remoteStage/remote-verify-substore.py"),
    '--app-dir', (Quote-Remote $SubStoreDir),
    '--data', (Quote-Remote "$SubStoreDir/data/sub-store.json"),
    '--container', (Quote-Remote $ContainerName)
  )
  if ($SkipHttp) { $cmd += '--skip-http' }

  Write-Info 'running remote read-only verification'
  & ssh @sshArgs ($cmd -join ' ')
  if ($LASTEXITCODE -ne 0) { throw 'remote verification failed' }
  Write-Ok 'remote verification passed'
} finally {
  & ssh @sshArgs ("rm -rf " + (Quote-Remote $remoteStage)) | Out-Null
}
