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
  [string]$SubStoreDataPath = '',
  [string]$SubStoreBackupDir = '',
  [string]$ContainerName = 'frontier-sub-store',
  [string]$CollectionName = 'merged-airports',
  [string]$MihomoFileName = 'frontier-chain-mihomo',
  [string]$ResidentialAggregatorUrl = $env:FRONTIER_RESIDENTIAL_AGGREGATOR_URL,
  [string]$ResidentialAggregatorName = 'aggregated-residential',
  [string]$ResidentialAggregatorDisplayName = '20-原料-家宽-聚合',
  [string]$ResidentialAggregatorSourcePrefix = 'AGG',
  [switch]$LinkExistingThreeX,
  [switch]$EnsureExistingEdgeUsV2,
  [switch]$LinkExistingEdgeUsV2,
  [string]$CloneEdgeUsV2AttFrom = $env:FRONTIER_EDGE_US_V2_ATT_SOURCE,
  [string]$EdgeUsV2VmessBundle = $env:FRONTIER_EDGE_US_V2_VMESS_BUNDLE,
  [string]$EdgeUsV2Hy2Bundle = $env:FRONTIER_EDGE_US_V2_HY2_BUNDLE,
  [switch]$UnlinkLegacySelfNodes,
  [string]$IosAirportsSubscriptions = $env:FRONTIER_IOS_AIRPORTS_SUBSCRIPTIONS,
  [string]$IosHy2Subscriptions = $env:FRONTIER_IOS_HY2_SUBSCRIPTIONS,
  [switch]$NoBackup,
  [switch]$NoRestart,

  [ValidateRange(30, 1800)]
  [int]$RemoteDeadlineSeconds = 300
)

$ErrorActionPreference = 'Stop'

if (-not $SshUser) { $SshUser = 'root' }
if (-not $SubStoreDataPath) { $SubStoreDataPath = "$SubStoreDir/data/sub-store.json" }
if (-not $SubStoreBackupDir) { $SubStoreBackupDir = "$SubStoreDir/backups" }

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
  if ($SshPort) { $args += @('-p', $SshPort) }
  $args += @(
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=10',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'ServerAliveInterval=15',
    '-o', 'ServerAliveCountMax=2',
    '-o', 'StrictHostKeyChecking=accept-new',
    "$SshUser@$SshHost"
  )
  return $args
}

function Get-ScpArgs {
  $args = @()
  if ($SshKey) { $args += @('-i', $SshKey) }
  if ($SshPort) { $args += @('-P', $SshPort) }
  $args += @(
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=10',
    '-o', 'IdentitiesOnly=yes',
    '-o', 'ServerAliveInterval=15',
    '-o', 'ServerAliveCountMax=2',
    '-o', 'StrictHostKeyChecking=accept-new'
  )
  return $args
}

function Quote-Remote($Text) {
  return "'" + $Text.Replace("'", "'\''") + "'"
}

function Invoke-BoundedSubStoreRemote {
  param(
    [Parameter(Mandatory = $true)][string[]]$SshArgs,
    [Parameter(Mandatory = $true)][string]$Body,
    [Parameter(Mandatory = $true)][string]$RemoteStage,
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][int]$DeadlineSeconds,
    [string]$SecretInputPath = ''
  )

  $resultPath = "$RemoteStage/result.env"
  $payloadPath = "$RemoteStage/payload"
  $windowName = "substore-$($RunId.Replace('-', ''))"
  $template = @'
#!/usr/bin/env bash
set -eu
RESULT_PATH=__RESULT_PATH__
PAYLOAD_PATH=__PAYLOAD_PATH__
ARCHIVE_PATH=__ARCHIVE_PATH__
SECRET_INPUT_PATH=__SECRET_INPUT_PATH__

finish() {
  rc=$?
  set +e
  trap - EXIT TERM INT HUP
  cleanup=1
  if [ -n "$SECRET_INPUT_PATH" ] && [ -e "$SECRET_INPUT_PATH" ]; then
    rm -f "$SECRET_INPUT_PATH"
    if [ -e "$SECRET_INPUT_PATH" ]; then
      cleanup=0
    fi
  fi
  result=failed
  gate=blocked
  if [ "$rc" -eq 0 ]; then
    mkdir -p "$(dirname "$ARCHIVE_PATH")"
    if ! mv "$PAYLOAD_PATH" "$ARCHIVE_PATH"; then
      rc=21
      cleanup=0
    else
      result=complete
      gate=pass
    fi
  elif [ "$rc" -eq 124 ]; then
    result=timeout
    cleanup=0
  else
    cleanup=0
  fi
  result_tmp="${RESULT_PATH}.tmp"
  printf 'SUBSTORE_DEPLOY_STARTED=1\nSUBSTORE_DEPLOY_EXIT=%s\nSUBSTORE_DEPLOY_RESULT=%s\nSUBSTORE_DEPLOY_GATE=%s\nSUBSTORE_DEPLOY_CLEANUP=%s\n' \
    "$rc" "$result" "$gate" "$cleanup" > "$result_tmp"
  mv "$result_tmp" "$RESULT_PATH"
  exit "$rc"
}

trap finish EXIT
trap 'exit 124' TERM
trap 'exit 130' INT HUP
printf 'SUBSTORE_DEPLOY_STARTED=1\n' > "$RESULT_PATH"

__BODY__
'@
  $script = $template.Replace('__RESULT_PATH__', (Quote-Remote $resultPath)).Replace('__PAYLOAD_PATH__', (Quote-Remote $payloadPath)).Replace('__ARCHIVE_PATH__', (Quote-Remote $ArchivePath)).Replace('__SECRET_INPUT_PATH__', (Quote-Remote $SecretInputPath)).Replace('__BODY__', $Body)
  $script = $script -replace "`r`n", "`n"
  $script = $script -replace "`r", "`n"
  $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($script))
  $launchTemplate = @'
set -eu
WINDOW_NAME=__WINDOW_NAME__
DEADLINE_SECONDS=__DEADLINE_SECONDS__
SCRIPT_B64=__SCRIPT_B64__
tmux has-session -t ai 2>/dev/null || tmux new-session -d -s ai
window_count=$(tmux list-windows -t ai -F '#{window_name}' | awk -v target="$WINDOW_NAME" '$0 == target { count++ } END { print count + 0 }')
printf 'SUBSTORE_DEPLOY_WINDOW_PRE=%s\n' "$window_count"
[ "$window_count" -eq 0 ]
tmux new-window -d -t ai -n "$WINDOW_NAME" "printf '%s' '$SCRIPT_B64' | base64 -d | timeout --foreground $DEADLINE_SECONDS bash -s >/dev/null 2>&1"
'@
  $launch = $launchTemplate.Replace('__WINDOW_NAME__', $windowName).Replace('__DEADLINE_SECONDS__', [string]$DeadlineSeconds).Replace('__SCRIPT_B64__', $encoded)
  $launch = $launch -replace "`r`n", "`n"
  $launch = $launch -replace "`r", "`n"
  $launchOutput = @(& ssh @SshArgs $launch)
  if ($LASTEXITCODE -ne 0 -or ($launchOutput -notcontains 'SUBSTORE_DEPLOY_WINDOW_PRE=0')) {
    throw 'remote Sub-Store tmux launch failed or the run window already exists'
  }

  $terminal = $null
  $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $localDeadlineSeconds = $DeadlineSeconds + 45
  while ($stopwatch.Elapsed.TotalSeconds -lt $localDeadlineSeconds) {
    $pollCommand = "if [ -s $(Quote-Remote $resultPath) ]; then cat $(Quote-Remote $resultPath); fi"
    $lines = @(& ssh @SshArgs $pollCommand)
    if ($LASTEXITCODE -ne 0) { throw 'remote Sub-Store result poll failed' }
    if ($lines -contains 'SUBSTORE_DEPLOY_RESULT=complete' -or $lines -contains 'SUBSTORE_DEPLOY_RESULT=failed' -or $lines -contains 'SUBSTORE_DEPLOY_RESULT=timeout') {
      $terminal = $lines
      break
    }
    Start-Sleep -Seconds 2
  }
  if (-not $terminal) {
    throw "remote Sub-Store deployment did not emit terminal markers within $localDeadlineSeconds seconds; stage preserved for inspection"
  }

  $markers = @{}
  foreach ($line in $terminal) {
    if ($line -match '^(SUBSTORE_DEPLOY_[A-Z_]+)=([A-Za-z0-9_-]+)$') {
      $markers[$Matches[1]] = $Matches[2]
    }
  }
  foreach ($required in @('SUBSTORE_DEPLOY_STARTED', 'SUBSTORE_DEPLOY_EXIT', 'SUBSTORE_DEPLOY_RESULT', 'SUBSTORE_DEPLOY_GATE', 'SUBSTORE_DEPLOY_CLEANUP')) {
    if (-not $markers.ContainsKey($required)) { throw "remote Sub-Store deployment marker missing: $required" }
  }

  $windowTemplate = @'
set -eu
WINDOW_NAME=__WINDOW_NAME__
window_count=$(tmux list-windows -t ai -F '#{window_name}' | awk -v target="$WINDOW_NAME" '$0 == target { count++ } END { print count + 0 }')
printf 'SUBSTORE_DEPLOY_WINDOW_POST=%s\n' "$window_count"
'@
  $windowCheck = $windowTemplate.Replace('__WINDOW_NAME__', $windowName)
  $windowOutput = @()
  for ($attempt = 1; $attempt -le 5; $attempt++) {
    $windowOutput = @(& ssh @SshArgs $windowCheck)
    if ($LASTEXITCODE -ne 0) { throw 'remote Sub-Store window residue check failed' }
    if ($windowOutput -contains 'SUBSTORE_DEPLOY_WINDOW_POST=0') { break }
    Start-Sleep -Seconds 1
  }
  if ($windowOutput -notcontains 'SUBSTORE_DEPLOY_WINDOW_POST=0') {
    throw 'remote Sub-Store window residue is non-zero; no cleanup was attempted'
  }

  foreach ($key in @('SUBSTORE_DEPLOY_STARTED', 'SUBSTORE_DEPLOY_EXIT', 'SUBSTORE_DEPLOY_RESULT', 'SUBSTORE_DEPLOY_GATE', 'SUBSTORE_DEPLOY_CLEANUP')) {
    Write-Host "$key=$($markers[$key])"
  }
  Write-Host 'SUBSTORE_DEPLOY_WINDOW_POST=0'

  $passed = $markers['SUBSTORE_DEPLOY_EXIT'] -eq '0' -and $markers['SUBSTORE_DEPLOY_RESULT'] -eq 'complete' -and $markers['SUBSTORE_DEPLOY_GATE'] -eq 'pass' -and $markers['SUBSTORE_DEPLOY_CLEANUP'] -eq '1'
  if (-not $passed) {
    throw 'remote Sub-Store deployment completed with a blocked terminal result; stage preserved for inspection'
  }

  & ssh @SshArgs ("rm -f " + (Quote-Remote $resultPath)) | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'remote Sub-Store result cleanup failed' }
  $stageCheck = @(& ssh @SshArgs ("rmdir " + (Quote-Remote $RemoteStage) + " 2>/dev/null || true; if [ -e " + (Quote-Remote $RemoteStage) + " ]; then echo SUBSTORE_DEPLOY_STAGE_RESIDUE=1; else echo SUBSTORE_DEPLOY_STAGE_RESIDUE=0; fi"))
  if ($LASTEXITCODE -ne 0 -or ($stageCheck -notcontains 'SUBSTORE_DEPLOY_STAGE_RESIDUE=0')) {
    throw 'remote Sub-Store stage residue is non-zero; no recursive cleanup was attempted'
  }
  Write-Host 'SUBSTORE_DEPLOY_STAGE_RESIDUE=0'
}

$selected = Get-SelectedTargets
if (($ResidentialAggregatorUrl -or $LinkExistingThreeX) -and -not ($selected -contains 'source-marker')) {
  $selected = @('source-marker') + $selected
}
if (-not $CloneEdgeUsV2AttFrom) { $CloneEdgeUsV2AttFrom = '' }

Write-Info "repo root: $RepoRoot"
Write-Info "targets: $($selected -join ', ')"
Write-Info "target Sub-Store dir: $SubStoreDir"
Write-Info "target collection: $CollectionName"
Write-Info "target mihomo file: $MihomoFileName"

foreach ($target in $selected) {
  Test-RequiredFile $Files[$target]
}
Test-RequiredFile $Files['remote-apply']
if ($EdgeUsV2VmessBundle -or $EdgeUsV2Hy2Bundle) {
  if (-not $EdgeUsV2VmessBundle -or -not $EdgeUsV2Hy2Bundle) {
    throw 'EdgeUsV2VmessBundle and EdgeUsV2Hy2Bundle must be passed together'
  }
  Test-RequiredFile $EdgeUsV2VmessBundle
  Test-RequiredFile $EdgeUsV2Hy2Bundle
}

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
if ($LinkExistingThreeX) {
    Write-Host ("  {0,-18} -> link existing sjc-3x/malaysia-3x/old-us-3x subscriptions" -f 'existing-3x')
  }
  if ($EnsureExistingEdgeUsV2) {
    Write-Host ("  {0,-18} -> ensure edge-us-v2 local subs and upstream collection" -f 'edge-us-v2')
  }
  if ($CloneEdgeUsV2AttFrom) {
    Write-Host ("  {0,-18} -> clone AT&T upstream into edge-us-v2-att from $CloneEdgeUsV2AttFrom" -f 'edge-us-v2-att')
  }
  if ($EdgeUsV2VmessBundle -or $EdgeUsV2Hy2Bundle) {
    Write-Host ("  {0,-18} -> patch edge-us-v2 local sub content from remote appliance bundles" -f 'edge-us-v2-content')
  }
  if ($LinkExistingEdgeUsV2) {
    Write-Host ("  {0,-18} -> link edge-us-v2-roles / edge-us-v2-hy2-roles into active collections" -f 'edge-us-v2-link')
  }
  if ($UnlinkLegacySelfNodes) {
    Write-Host ("  {0,-18} -> unlink old SJC/Malaysia generated/local refs from active collections" -f 'legacy-unlink')
  }
  if ($IosAirportsSubscriptions) {
    Write-Host ("  {0,-18} -> explicit subscription list" -f 'ios-airports-uri')
  }
  if ($IosHy2Subscriptions) {
    Write-Host ("  {0,-18} -> explicit subscription list" -f 'ios-hy2')
  }
  exit 0
}

if (-not $SshHost) {
  throw 'FRONTIER_SUBSTORE_SSH_HOST or -SshHost is required when using -Apply'
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$remoteStage = "/tmp/frontier-substore-deploy-$runId"
$remotePayload = "$remoteStage/payload"
$sshArgs = Get-SshArgs
$scpArgs = Get-ScpArgs

Write-Info "creating remote staging: $remoteStage"
& ssh @sshArgs ("mkdir -p " + (Quote-Remote $remotePayload))
if ($LASTEXITCODE -ne 0) { throw 'ssh mkdir failed' }

$uploadMap = @{
  'remote-apply'       = 'remote-apply-substore.py'
  'source-marker'      = 'substore-source-marker.js'
  'nodes'              = 'shadowrocket-nodes-injector.js'
  'mihomo'             = 'main.js'
  'powerfullz-updater' = 'update-powerfullz-inline.py'
}

$uploadKeys = @('remote-apply') + $selected
foreach ($key in $uploadKeys) {
  $remotePath = "${SshUser}@${SshHost}:$remotePayload/$($uploadMap[$key])"
  Write-Info "uploading $key"
  & scp @scpArgs $Files[$key] $remotePath
  if ($LASTEXITCODE -ne 0) { throw "scp failed: $key" }
}

$remoteEdgeUsV2VmessBundle = ''
$remoteEdgeUsV2Hy2Bundle = ''
if ($EdgeUsV2VmessBundle -and $EdgeUsV2Hy2Bundle) {
  $remoteEdgeUsV2VmessBundle = "$remotePayload/edge-us-v2-vmess-bundle.txt"
  $remoteEdgeUsV2Hy2Bundle = "$remotePayload/edge-us-v2-hy2-bundle.txt"
  Write-Info 'uploading edge-us-v2 bundles'
  & scp @scpArgs $EdgeUsV2VmessBundle "${SshUser}@${SshHost}:$remoteEdgeUsV2VmessBundle"
  if ($LASTEXITCODE -ne 0) { throw 'scp failed: edge-us-v2 vmess bundle' }
  & scp @scpArgs $EdgeUsV2Hy2Bundle "${SshUser}@${SshHost}:$remoteEdgeUsV2Hy2Bundle"
  if ($LASTEXITCODE -ne 0) { throw 'scp failed: edge-us-v2 hy2 bundle' }
}

$cmd = @(
  'python3',
  (Quote-Remote "$remotePayload/remote-apply-substore.py"),
  '--app-dir', (Quote-Remote $SubStoreDir),
  '--data', (Quote-Remote $SubStoreDataPath),
  '--backup-dir', (Quote-Remote $SubStoreBackupDir),
  '--container', (Quote-Remote $ContainerName),
  '--collection', (Quote-Remote $CollectionName),
  '--file', (Quote-Remote $MihomoFileName)
)

  if ($selected -contains 'source-marker') {
    $cmd += @('--source-marker', (Quote-Remote "$remotePayload/substore-source-marker.js"))
  }
  if ($selected -contains 'nodes') {
    $cmd += @('--nodes-injector', (Quote-Remote "$remotePayload/shadowrocket-nodes-injector.js"))
  }
  if ($selected -contains 'mihomo') {
    $cmd += @('--mihomo-main', (Quote-Remote "$remotePayload/main.js"))
  }
  if ($selected -contains 'powerfullz-updater') {
    $cmd += @('--powerfullz-updater', (Quote-Remote "$remotePayload/update-powerfullz-inline.py"))
  }
  if ($ResidentialAggregatorUrl) {
    $cmd += @(
      '--aggregator-url-stdin',
      '--aggregator-name', (Quote-Remote $ResidentialAggregatorName),
      '--aggregator-display-name', (Quote-Remote $ResidentialAggregatorDisplayName),
      '--aggregator-source-prefix', (Quote-Remote $ResidentialAggregatorSourcePrefix)
    )
  }
  if ($LinkExistingThreeX) {
    $cmd += '--link-existing-three-x'
  }
  if ($EnsureExistingEdgeUsV2) {
    $cmd += '--ensure-existing-edge-us-v2'
  }
  if ($CloneEdgeUsV2AttFrom) {
    $cmd += @('--clone-edge-us-v2-att-from', (Quote-Remote $CloneEdgeUsV2AttFrom))
  }
  if ($remoteEdgeUsV2VmessBundle -or $remoteEdgeUsV2Hy2Bundle) {
    $cmd += @(
      '--edge-us-v2-vmess-bundle', (Quote-Remote $remoteEdgeUsV2VmessBundle),
      '--edge-us-v2-hy2-bundle', (Quote-Remote $remoteEdgeUsV2Hy2Bundle)
    )
  }
  if ($LinkExistingEdgeUsV2) {
    $cmd += '--link-existing-edge-us-v2'
  }
  if ($UnlinkLegacySelfNodes) {
    $cmd += '--unlink-legacy-self-nodes'
  }
  if ($IosAirportsSubscriptions) {
    $cmd += @('--ios-airports-subscriptions', (Quote-Remote $IosAirportsSubscriptions))
  }
  if ($IosHy2Subscriptions) {
    $cmd += @('--ios-hy2-subscriptions', (Quote-Remote $IosHy2Subscriptions))
  }
  if ($NoBackup) { $cmd += '--no-backup' }
  if ($NoRestart) { $cmd += '--no-restart' }

Write-Info 'applying remote patch inside bounded tmux'
$secretInputPath = ''
$remoteBody = $cmd -join ' '
if ($ResidentialAggregatorUrl) {
  $secretInputPath = "$remotePayload/aggregator-url.txt"
  $localSecret = New-TemporaryFile
  try {
    [System.IO.File]::WriteAllText($localSecret.FullName, $ResidentialAggregatorUrl + "`n", (New-Object System.Text.UTF8Encoding($false)))
    & scp @scpArgs $localSecret.FullName "${SshUser}@${SshHost}:$secretInputPath"
    if ($LASTEXITCODE -ne 0) { throw 'scp failed: residential aggregator input' }
  } finally {
    Remove-Item -LiteralPath $localSecret.FullName -ErrorAction SilentlyContinue
  }
  $remoteBody += " < " + (Quote-Remote $secretInputPath)
}
$archivePath = "$SubStoreBackupDir/deploy-payload-$runId"
Invoke-BoundedSubStoreRemote -SshArgs $sshArgs -Body $remoteBody -RemoteStage $remoteStage -ArchivePath $archivePath -RunId $runId -DeadlineSeconds $RemoteDeadlineSeconds -SecretInputPath $secretInputPath
Write-Ok 'Sub-Store deploy finished'
