<#
.SYNOPSIS
  Deploy non-secret frontier-chain-skeleton scripts into VPS Sub-Store.

.DESCRIPTION
  The repository is the source of truth for script bodies. The VPS keeps the
  runtime state and secrets in sub-store.json. This deployer updates only Script
  Operator content and preserves existing arguments, tokens, and subscription
  URLs.

  Default mode is dry-run: local syntax checks and a deployment plan only.
  Use -Apply to upload files, patch sub-store.json, back it up, and restart the
  Sub-Store container.

.EXAMPLE
  .\scripts\deploy-substore.ps1
  # dry-run only

.EXAMPLE
  .\scripts\deploy-substore.ps1 -Apply -Targets nodes,mihomo
  # update collection normalizer and final mihomo custom script
#>

[CmdletBinding()]
param(
  [switch]$Apply,

  [ValidateSet('all', 'source-marker', 'nodes', 'mihomo', 'powerfullz-updater')]
  [string[]]$Targets = @('all'),

  [string]$SshHost = $env:FRONTIER_SUBSTORE_SSH_HOST,
  [string]$SshPort = $env:FRONTIER_SUBSTORE_SSH_PORT,
  [string]$SshUser = $env:FRONTIER_SUBSTORE_SSH_USER,
  [string]$SshKey = $env:FRONTIER_SUBSTORE_SSH_KEY,

  [string]$SubStoreDir = '/opt/1panel/apps/sub-store/sub-store',
  [string]$ContainerName = 'sub-store',
  [string]$ResidentialAggregatorUrl = $env:FRONTIER_RESIDENTIAL_AGGREGATOR_URL,
  [string]$ResidentialAggregatorName = 'aggregated-residential',
  [string]$ResidentialAggregatorDisplayName = '家宽聚合订阅',
  [string]$ResidentialAggregatorSourcePrefix = 'AGG',
  [switch]$NoBackup,
  [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'

if (-not $SshPort) { $SshPort = '22' }
if (-not $SshUser) { $SshUser = 'root' }

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Files = @{
  'source-marker'      = Join-Path $RepoRoot 'substore-source-marker.js'
  'nodes'              = Join-Path $RepoRoot 'shadowrocket-nodes-injector.js'
  'mihomo'             = Join-Path $RepoRoot 'main.js'
  'powerfullz-updater' = Join-Path $PSScriptRoot 'update-powerfullz-inline.py'
  'remote-apply'       = Join-Path $PSScriptRoot 'remote-apply-substore.py'
}

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

function Test-RequiredFile($Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "missing required file: $Path"
  }
  if ((Get-Item -LiteralPath $Path).Length -le 0) {
    throw "empty required file: $Path"
  }
}

function Invoke-NodeCheck($Path) {
  $node = Get-Command node -ErrorAction SilentlyContinue
  if (-not $node) {
    Write-Warn2 "node not found, skipped syntax check: $Path"
    return
  }
  & node --check $Path
  if ($LASTEXITCODE -ne 0) {
    throw "node --check failed: $Path"
  }
  Write-Ok "node --check passed: $(Split-Path -Leaf $Path)"
}

function Get-SelectedTargets {
  if ($Targets -contains 'all') {
    return @('source-marker', 'nodes', 'mihomo', 'powerfullz-updater')
  }
  return $Targets
}

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

$selected = Get-SelectedTargets
if ($ResidentialAggregatorUrl -and -not ($selected -contains 'source-marker')) {
  $selected = @('source-marker') + $selected
}

Write-Info "repo root: $RepoRoot"
Write-Info "targets: $($selected -join ', ')"

foreach ($target in $selected) {
  Test-RequiredFile $Files[$target]
}
Test-RequiredFile $Files['remote-apply']

foreach ($target in @('source-marker', 'nodes', 'mihomo')) {
  if ($selected -contains $target) {
    Invoke-NodeCheck $Files[$target]
  }
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
  & python -B -c "import pathlib,sys; [compile(pathlib.Path(p).read_text(encoding='utf-8'), p, 'exec') for p in sys.argv[1:]]" $Files['remote-apply'] $Files['powerfullz-updater']
  if ($LASTEXITCODE -ne 0) { throw 'python syntax check failed' }
  Write-Ok 'python syntax checks passed'
} else {
  Write-Warn2 'python not found locally, skipped Python syntax checks'
}

if (-not $Apply) {
  Write-Host ''
  Write-Host '===== DRY RUN =====' -ForegroundColor Magenta
  Write-Host 'No VPS files were changed. Re-run with -Apply to patch Sub-Store.'
  foreach ($target in $selected) {
    Write-Host ("  {0,-18} -> {1}" -f $target, $Files[$target])
  }
  if ($ResidentialAggregatorUrl) {
    Write-Host ("  {0,-18} -> {1} ({2})" -f 'residential-upstream', $ResidentialAggregatorName, $ResidentialAggregatorSourcePrefix)
  }
  exit 0
}

if (-not $SshHost) {
  throw 'FRONTIER_SUBSTORE_SSH_HOST or -SshHost is required when using -Apply'
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$remoteStage = "/tmp/frontier-substore-deploy-$runId"
$sshArgs = Get-SshArgs
$scpArgs = Get-ScpArgs

Write-Info "creating remote staging: $remoteStage"
& ssh @sshArgs ("mkdir -p " + (Quote-Remote $remoteStage))
if ($LASTEXITCODE -ne 0) { throw 'ssh mkdir failed' }

try {
  $uploadMap = @{
    'remote-apply'       = 'remote-apply-substore.py'
    'source-marker'      = 'substore-source-marker.js'
    'nodes'              = 'shadowrocket-nodes-injector.js'
    'mihomo'             = 'main.js'
    'powerfullz-updater' = 'update-powerfullz-inline.py'
  }

  $uploadKeys = @('remote-apply') + $selected
  foreach ($key in $uploadKeys) {
    $remotePath = "${SshUser}@${SshHost}:$remoteStage/$($uploadMap[$key])"
    Write-Info "uploading $key"
    & scp @scpArgs $Files[$key] $remotePath
    if ($LASTEXITCODE -ne 0) { throw "scp failed: $key" }
  }

  $cmd = @(
    'python3',
    (Quote-Remote "$remoteStage/remote-apply-substore.py"),
    '--app-dir', (Quote-Remote $SubStoreDir),
    '--data', (Quote-Remote "$SubStoreDir/data/sub-store.json"),
    '--backup-dir', (Quote-Remote "$SubStoreDir/backups"),
    '--container', (Quote-Remote $ContainerName)
  )

  if ($selected -contains 'source-marker') {
    $cmd += @('--source-marker', (Quote-Remote "$remoteStage/substore-source-marker.js"))
  }
  if ($selected -contains 'nodes') {
    $cmd += @('--nodes-injector', (Quote-Remote "$remoteStage/shadowrocket-nodes-injector.js"))
  }
  if ($selected -contains 'mihomo') {
    $cmd += @('--mihomo-main', (Quote-Remote "$remoteStage/main.js"))
  }
  if ($selected -contains 'powerfullz-updater') {
    $cmd += @('--powerfullz-updater', (Quote-Remote "$remoteStage/update-powerfullz-inline.py"))
  }
  if ($ResidentialAggregatorUrl) {
    $cmd += @(
      '--aggregator-url-stdin',
      '--aggregator-name', (Quote-Remote $ResidentialAggregatorName),
      '--aggregator-display-name', (Quote-Remote $ResidentialAggregatorDisplayName),
      '--aggregator-source-prefix', (Quote-Remote $ResidentialAggregatorSourcePrefix)
    )
  }
  if ($NoBackup) { $cmd += '--no-backup' }
  if ($NoRestart) { $cmd += '--no-restart' }

  Write-Info 'applying remote patch'
  if ($ResidentialAggregatorUrl) {
    $ResidentialAggregatorUrl | & ssh @sshArgs ($cmd -join ' ')
  } else {
    & ssh @sshArgs ($cmd -join ' ')
  }
  if ($LASTEXITCODE -ne 0) { throw 'remote apply failed' }
  Write-Ok 'Sub-Store deploy finished'
} finally {
  Write-Info 'cleaning remote staging'
  $cleanupCmd = @(
    'rm -f ' + (Quote-Remote "$remoteStage/remote-apply-substore.py"),
    'rm -f ' + (Quote-Remote "$remoteStage/substore-source-marker.js"),
    'rm -f ' + (Quote-Remote "$remoteStage/shadowrocket-nodes-injector.js"),
    'rm -f ' + (Quote-Remote "$remoteStage/main.js"),
    'rm -f ' + (Quote-Remote "$remoteStage/update-powerfullz-inline.py"),
    'rmdir ' + (Quote-Remote $remoteStage) + ' 2>/dev/null || true'
  ) -join ' && '
  & ssh @sshArgs $cleanupCmd | Out-Null
}
