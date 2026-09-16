<#
.SYNOPSIS
  Render and deploy the SJC subscription entry hub.

.DESCRIPTION
  Default mode is dry-run. With -Apply, this script uploads generated static
  files and a standalone managed Caddy site block to SJC, then asks the
  existing ingress container to reload. It does not mutate Sub-Store objects.
  With -RulesOnly -Apply, it updates and runs only the rule mirror runtime;
  client-facing configuration and ingress files are left unchanged.
  With -Disable -Apply, it removes the managed block and stops the sidecar.
#>

[CmdletBinding()]
param(
  [switch]$Apply,
  [switch]$Disable,
  [switch]$RulesOnly,

  [ValidateRange(30, 1800)]
  [int]$RemoteDeadlineSeconds = 300,

  [string]$SshHost = $env:FRONTIER_SUBSTORE_SSH_HOST,
  [string]$SshPort = $env:FRONTIER_SUBSTORE_SSH_PORT,
  [string]$SshUser = $env:FRONTIER_SUBSTORE_SSH_USER,
  [string]$SshKey = $env:FRONTIER_SUBSTORE_SSH_KEY,

  [string]$EnvFile = $env:HUB_RUNTIME_ENV,
  [string]$RemoteRoot = '/opt/frontier/subscription-hub',
  [ValidateSet('caddy', 'openresty')]
  [string]$IngressKind = $env:HUB_INGRESS_KIND,
  [string]$CaddyContainer = $env:HUB_CADDY_CONTAINER,
  [string]$CaddyfilePath = $env:HUB_CADDYFILE_PATH,
  [string]$SubStoreContainer = $env:HUB_SUBSTORE_CONTAINER,
  [string]$HubContainerName = $env:HUB_CONTAINER_NAME,
  [string]$HubDockerNetwork = $env:HUB_DOCKER_NETWORK,
  [string]$HubCaddyImage = $env:HUB_CADDY_IMAGE,
  [string]$LegacyRedirectHosts = $env:HUB_LEGACY_REDIRECT_HOSTS,
  [string]$OpenRestyContainer = $env:HUB_OPENRESTY_CONTAINER,
  [string]$OpenRestyConfPath = $env:HUB_OPENRESTY_CONF_PATH
)

$ErrorActionPreference = 'Stop'

if (-not $SshUser) { $SshUser = 'root' }

$HubDir = Split-Path -Parent $PSCommandPath
$RepoRoot = Split-Path -Parent $HubDir
if (-not $EnvFile) { $EnvFile = Join-Path $HubDir 'examples\hub.runtime.env.example' }
$OutDir = Join-Path $HubDir '.secrets.local\out'

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

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

function Read-EnvValue($Path, $Key) {
  if (-not (Test-Path -LiteralPath $Path)) { return '' }
  foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
    if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") {
      return $Matches[1].Trim().Trim('"').Trim("'")
    }
  }
  return ''
}

function Write-Utf8NoBom($Path, [string[]]$Lines) {
  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, (($Lines -join [Environment]::NewLine) + [Environment]::NewLine), $encoding)
}

function Copy-EnvWithOverrides($SourcePath, $DestinationPath, [hashtable]$Overrides) {
  $lines = Get-Content -LiteralPath $SourcePath -Encoding UTF8
  $seen = @{}
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($line in $lines) {
    if ($line -match '^\s*([A-Z0-9_]+)=') {
      $key = $Matches[1]
      if ($Overrides.ContainsKey($key)) {
        $out.Add("$key=$($Overrides[$key])")
        $seen[$key] = $true
        continue
      }
    }
    $out.Add($line)
  }
  foreach ($key in $Overrides.Keys) {
    if (-not $seen.ContainsKey($key)) {
      $out.Add("$key=$($Overrides[$key])")
    }
  }
  Write-Utf8NoBom $DestinationPath $out.ToArray()
}

function New-BoundedRemoteRunner {
  param(
    [Parameter(Mandatory = $true)][string]$Body,
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$ResultPath
  )

  $template = @'
#!/usr/bin/env bash
set -eu
RESULT_PATH=__RESULT_PATH__
BUNDLE_PATH=__BUNDLE_PATH__

finish() {
  rc=$?
  set +e
  trap - EXIT TERM INT HUP
  cleanup=1
  if [ -e "$BUNDLE_PATH" ]; then
    rm -f "$BUNDLE_PATH"
    if [ -e "$BUNDLE_PATH" ]; then
      cleanup=0
    fi
  fi
  result=failed
  gate=blocked
  if [ "$rc" -eq 0 ]; then
    result=complete
    gate=pass
  elif [ "$rc" -eq 124 ]; then
    result=timeout
  fi
  result_tmp="${RESULT_PATH}.tmp"
  printf 'HUB_DEPLOY_STARTED=1\nHUB_DEPLOY_EXIT=%s\nHUB_DEPLOY_RESULT=%s\nHUB_DEPLOY_GATE=%s\nHUB_DEPLOY_CLEANUP=%s\n' \
    "$rc" "$result" "$gate" "$cleanup" > "$result_tmp"
  mv "$result_tmp" "$RESULT_PATH"
  exit "$rc"
}

trap finish EXIT
trap 'exit 124' TERM
trap 'exit 130' INT HUP
printf 'HUB_DEPLOY_STARTED=1\n' > "$RESULT_PATH"

__BODY__
'@

  return $template.Replace('__RESULT_PATH__', (Quote-Remote $ResultPath)).Replace('__BUNDLE_PATH__', (Quote-Remote $BundlePath)).Replace('__BODY__', $Body)
}

function Invoke-BoundedRemoteBundle {
  param(
    [Parameter(Mandatory = $true)][string[]]$SshArgs,
    [Parameter(Mandatory = $true)][string]$RemoteStage,
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][int]$DeadlineSeconds
  )

  $bundlePath = "$RemoteStage/subscription-hub-bundle.tgz"
  $resultPath = "$RemoteStage/result.env"
  $windowName = "hub-$($RunId.Replace('-', ''))"
  $launchTemplate = @'
set -eu
BUNDLE_PATH=__BUNDLE_PATH__
WINDOW_NAME=__WINDOW_NAME__
DEADLINE_SECONDS=__DEADLINE_SECONDS__
tmux has-session -t ai 2>/dev/null || tmux new-session -d -s ai
window_count=$(tmux list-windows -t ai -F '#{window_name}' | awk -v target="$WINDOW_NAME" '$0 == target { count++ } END { print count + 0 }')
printf 'HUB_DEPLOY_WINDOW_PRE=%s\n' "$window_count"
[ "$window_count" -eq 0 ]
tmux new-window -d -t ai -n "$WINDOW_NAME" "tar -xOf '$BUNDLE_PATH' deploy.sh | timeout --foreground $DEADLINE_SECONDS bash -s >/dev/null 2>&1"
'@
  $launch = $launchTemplate.Replace('__BUNDLE_PATH__', $bundlePath).Replace('__WINDOW_NAME__', $windowName).Replace('__DEADLINE_SECONDS__', [string]$DeadlineSeconds)

  $launchOutput = & ssh @SshArgs $launch
  if ($LASTEXITCODE -ne 0 -or ($launchOutput -notcontains 'HUB_DEPLOY_WINDOW_PRE=0')) {
    throw 'remote tmux launch failed or the run window already exists'
  }

  $terminal = $null
  $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
  $localDeadlineSeconds = $DeadlineSeconds + 45
  while ($stopwatch.Elapsed.TotalSeconds -lt $localDeadlineSeconds) {
    $pollCommand = "if [ -s $(Quote-Remote $resultPath) ]; then cat $(Quote-Remote $resultPath); fi"
    $lines = @(& ssh @SshArgs $pollCommand)
    if ($LASTEXITCODE -ne 0) { throw 'remote result poll failed' }
    if ($lines -contains 'HUB_DEPLOY_RESULT=complete' -or $lines -contains 'HUB_DEPLOY_RESULT=failed' -or $lines -contains 'HUB_DEPLOY_RESULT=timeout') {
      $terminal = $lines
      break
    }
    Start-Sleep -Seconds 2
  }
  if (-not $terminal) {
    throw "remote deployment did not emit terminal markers within $localDeadlineSeconds seconds; stage preserved for inspection"
  }

  $markers = @{}
  foreach ($line in $terminal) {
    if ($line -match '^(HUB_DEPLOY_[A-Z_]+)=([A-Za-z0-9_-]+)$') {
      $markers[$Matches[1]] = $Matches[2]
    }
  }
  foreach ($required in @('HUB_DEPLOY_STARTED', 'HUB_DEPLOY_EXIT', 'HUB_DEPLOY_RESULT', 'HUB_DEPLOY_GATE', 'HUB_DEPLOY_CLEANUP')) {
    if (-not $markers.ContainsKey($required)) { throw "remote deployment marker missing: $required" }
  }

  $windowCheckTemplate = @'
set -eu
WINDOW_NAME=__WINDOW_NAME__
window_count=$(tmux list-windows -t ai -F '#{window_name}' | awk -v target="$WINDOW_NAME" '$0 == target { count++ } END { print count + 0 }')
printf 'HUB_DEPLOY_WINDOW_POST=%s\n' "$window_count"
'@
  $windowCheck = $windowCheckTemplate.Replace('__WINDOW_NAME__', $windowName)
  $windowOutput = @()
  for ($attempt = 1; $attempt -le 5; $attempt++) {
    $windowOutput = @(& ssh @SshArgs $windowCheck)
    if ($LASTEXITCODE -ne 0) { throw 'remote deployment window residue check failed' }
    if ($windowOutput -contains 'HUB_DEPLOY_WINDOW_POST=0') { break }
    Start-Sleep -Seconds 1
  }
  if ($windowOutput -notcontains 'HUB_DEPLOY_WINDOW_POST=0') {
    throw 'remote deployment window residue is non-zero; no cleanup was attempted'
  }

  & ssh @SshArgs ("rm -f " + (Quote-Remote $resultPath)) | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'remote result cleanup failed' }
  $stageCheck = @(& ssh @SshArgs ("rmdir " + (Quote-Remote $RemoteStage) + " 2>/dev/null || true; if [ -e " + (Quote-Remote $RemoteStage) + " ]; then echo HUB_DEPLOY_STAGE_RESIDUE=1; else echo HUB_DEPLOY_STAGE_RESIDUE=0; fi"))
  if ($LASTEXITCODE -ne 0 -or ($stageCheck -notcontains 'HUB_DEPLOY_STAGE_RESIDUE=0')) {
    throw 'remote deployment stage residue is non-zero; no recursive cleanup was attempted'
  }

  foreach ($key in @('HUB_DEPLOY_STARTED', 'HUB_DEPLOY_EXIT', 'HUB_DEPLOY_RESULT', 'HUB_DEPLOY_GATE', 'HUB_DEPLOY_CLEANUP')) {
    Write-Host "$key=$($markers[$key])"
  }
  Write-Host 'HUB_DEPLOY_WINDOW_POST=0'
  Write-Host 'HUB_DEPLOY_STAGE_RESIDUE=0'

  if ($markers['HUB_DEPLOY_EXIT'] -ne '0' -or $markers['HUB_DEPLOY_RESULT'] -ne 'complete' -or $markers['HUB_DEPLOY_GATE'] -ne 'pass' -or $markers['HUB_DEPLOY_CLEANUP'] -ne '1') {
    throw 'remote deployment completed with a blocked terminal result'
  }
}

if ($Disable -and $RulesOnly) { throw '-Disable and -RulesOnly are mutually exclusive' }

if (-not (Test-Path -LiteralPath $EnvFile)) { throw "missing env file: $EnvFile" }

$envRemoteRoot = Read-EnvValue $EnvFile 'HUB_REMOTE_ROOT'
if ($envRemoteRoot) { $RemoteRoot = $envRemoteRoot }
if (-not $IngressKind) { $IngressKind = Read-EnvValue $EnvFile 'HUB_INGRESS_KIND' }
if (-not $IngressKind) { $IngressKind = 'caddy' }
if (-not $CaddyContainer) { $CaddyContainer = Read-EnvValue $EnvFile 'HUB_CADDY_CONTAINER' }
if (-not $CaddyfilePath) { $CaddyfilePath = Read-EnvValue $EnvFile 'HUB_CADDYFILE_PATH' }
if (-not $SubStoreContainer) { $SubStoreContainer = Read-EnvValue $EnvFile 'HUB_SUBSTORE_CONTAINER' }
if (-not $SubStoreContainer) { $SubStoreContainer = 'frontier-sub-store' }
if (-not $HubContainerName) { $HubContainerName = Read-EnvValue $EnvFile 'HUB_CONTAINER_NAME' }
if (-not $HubContainerName) { $HubContainerName = 'subscription-hub' }
if (-not $HubDockerNetwork) { $HubDockerNetwork = Read-EnvValue $EnvFile 'HUB_DOCKER_NETWORK' }
if (-not $HubDockerNetwork) { $HubDockerNetwork = 'frontier-sub-store_default' }
if (-not $HubCaddyImage) { $HubCaddyImage = Read-EnvValue $EnvFile 'HUB_CADDY_IMAGE' }
if (-not $HubCaddyImage) { $HubCaddyImage = 'caddy:2.10.0-alpine' }
if (-not $LegacyRedirectHosts) { $LegacyRedirectHosts = Read-EnvValue $EnvFile 'HUB_LEGACY_REDIRECT_HOSTS' }
if (-not $OpenRestyContainer) { $OpenRestyContainer = Read-EnvValue $EnvFile 'HUB_OPENRESTY_CONTAINER' }
if (-not $OpenRestyConfPath) { $OpenRestyConfPath = Read-EnvValue $EnvFile 'HUB_OPENRESTY_CONF_PATH' }
$PublicBaseUrl = Read-EnvValue $EnvFile 'HUB_PUBLIC_BASE_URL'
if (-not $PublicBaseUrl) { throw 'HUB_PUBLIC_BASE_URL is required' }
$PublicHost = ([Uri]$PublicBaseUrl).Host
$HubPagePath = Read-EnvValue $EnvFile 'HUB_PAGE_PATH'
if (-not $HubPagePath) { throw 'HUB_PAGE_PATH is required' }

Write-Info "repo root: $RepoRoot"
Write-Info "env file: $EnvFile"
Write-Info "remote root: $RemoteRoot"
Write-Info "public host: $PublicHost"
Write-Info "ingress kind: $IngressKind"
Write-Info "caddy container: $(if($CaddyContainer){'configured'}else{'missing'})"
Write-Info "caddyfile path: $(if($CaddyfilePath){'configured'}else{'missing'})"
Write-Info "sub-store container: $(if($SubStoreContainer){'configured'}else{'missing'})"
Write-Info "hub container: $HubContainerName"
Write-Info "hub docker network: $HubDockerNetwork"
Write-Info "legacy redirect hosts: $(if($LegacyRedirectHosts){'configured'}else{'missing'})"
Write-Info "openresty container: $(if($OpenRestyContainer){'configured'}else{'missing'})"
Write-Info "openresty conf path: $(if($OpenRestyConfPath){'configured'}else{'missing'})"

if ($Disable) {
  if ($IngressKind -ne 'caddy') { throw '-Disable is currently supported only for caddy ingress' }
  if (-not $Apply) {
    Write-Host ''
    Write-Host '===== DISABLE DRY RUN =====' -ForegroundColor Magenta
    Write-Host 'No remote files were changed. Re-run with -Disable -Apply to remove the managed Caddy block and stop the hub sidecar.'
    exit 0
  }
  if (-not $SshHost) { throw 'FRONTIER_SUBSTORE_SSH_HOST or -SshHost is required with -Disable -Apply' }
  if (-not $CaddyContainer) { throw 'HUB_CADDY_CONTAINER is required with -Disable -Apply' }
  if (-not $CaddyfilePath) { throw 'HUB_CADDYFILE_PATH is required with -Disable -Apply' }

  $runId = Get-Date -Format 'yyyyMMdd-HHmmss'
  $sshArgs = Get-SshArgs
  $remoteScript = @"
set -eu
umask 077
REMOTE_ROOT=$(Quote-Remote $RemoteRoot)
CADDYFILE=$(Quote-Remote $CaddyfilePath)
CONTAINER=$(Quote-Remote $CaddyContainer)
HUB_CONTAINER=$(Quote-Remote $HubContainerName)
STAMP=$runId
mkdir -p "`$REMOTE_ROOT/backups"
if [ -f "`$CADDYFILE" ]; then
  cp -p "`$CADDYFILE" "`$REMOTE_ROOT/backups/Caddyfile.bak-disable-`$STAMP"
fi
python3 - "`$CADDYFILE" <<'PY'
from pathlib import Path
import sys
conf = Path(sys.argv[1])
text = conf.read_text(encoding='utf-8') if conf.exists() else ''
start = '# BEGIN subscription-hub managed block'
end = '# END subscription-hub managed block'
removed = 0
while start in text and end in text:
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    text = before.rstrip() + '\n' + after.lstrip()
    removed += 1
conf.write_text(text, encoding='utf-8')
print('managed_blocks_removed=' + str(removed))
PY
docker rm -f "`$HUB_CONTAINER" >/dev/null 2>&1 || true
if ! docker exec "`$CONTAINER" caddy validate --config /etc/caddy/Caddyfile >/dev/null; then
  cp -p "`$REMOTE_ROOT/backups/Caddyfile.bak-disable-`$STAMP" "`$CADDYFILE"
  echo "ERROR: Caddy validate failed; restored backup" >&2
  exit 3
fi
docker exec "`$CONTAINER" caddy reload --config /etc/caddy/Caddyfile >/dev/null
echo "hub_container=stopped"
echo "caddy_reload=ok"
"@
  Write-Info 'disabling managed subscription hub block on remote host'
  & ssh @sshArgs $remoteScript
  if ($LASTEXITCODE -ne 0) { throw 'remote disable failed' }
  Write-Ok 'remote disable completed'
  exit 0
}

$RenderEnvFile = $EnvFile
if ($IngressKind -eq 'caddy') {
  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
  $RenderEnvFile = Join-Path $OutDir 'hub.runtime.render.env'
  Copy-EnvWithOverrides $EnvFile $RenderEnvFile @{
    HUB_LOCAL_SERVICE_URL = "http://${HubContainerName}:19180"
  }
  Write-Info 'caddy hub upstream: sidecar container'
}

$renderArgs = @((Join-Path $HubDir 'render.py'), '--env-file', $RenderEnvFile, '--out-dir', $OutDir)
if ($Apply) { $renderArgs += '--strict' }
& python @renderArgs
if ($LASTEXITCODE -ne 0) { throw 'render failed' }
Write-Ok 'rendered hub artifacts'

if (-not $Apply) {
  Write-Host ''
  Write-Host $(if ($RulesOnly) { '===== RULES-ONLY DRY RUN =====' } else { '===== DRY RUN =====' }) -ForegroundColor Magenta
  Write-Host $(if ($RulesOnly) { 'No remote files were changed. Re-run with -RulesOnly -Apply to publish only the mirror runtime.' } else { 'No remote files were changed. Re-run with -Apply after reviewing the plan.' })
  Get-Content -LiteralPath (Join-Path $OutDir 'manifest.safe.json') -Encoding UTF8
  exit 0
}

if (-not $SshHost) { throw 'FRONTIER_SUBSTORE_SSH_HOST or -SshHost is required with -Apply' }
if (-not $RulesOnly) {
  if ($IngressKind -eq 'caddy') {
    if (-not $CaddyContainer) { throw 'HUB_CADDY_CONTAINER is required with -Apply' }
    if (-not $CaddyfilePath) { throw 'HUB_CADDYFILE_PATH is required with -Apply' }
  } else {
    if (-not $OpenRestyContainer) { throw 'HUB_OPENRESTY_CONTAINER is required with -Apply' }
    if (-not $OpenRestyConfPath) { throw 'HUB_OPENRESTY_CONF_PATH is required with -Apply' }
  }
}

$runId = Get-Date -Format 'yyyyMMdd-HHmmss'
$remoteStage = "/tmp/subscription-hub-$runId"
$sshArgs = Get-SshArgs
$scpArgs = Get-ScpArgs

if ($RulesOnly) {
  $remoteBundle = "$remoteStage/subscription-hub-bundle.tgz"
  $remoteResult = "$remoteStage/result.env"
  $remoteScript = @"
set -eu
umask 077
REMOTE_ROOT=$(Quote-Remote $RemoteRoot)
BUNDLE=$(Quote-Remote $remoteBundle)
STAMP=$runId
APP_BACKUP="`$REMOTE_ROOT/backups/app.bak-rules-`$STAMP.tgz"
RULES_BACKUP="`$REMOTE_ROOT/backups/rules.bak-rules-`$STAMP.tgz"
SERVICE_BACKUP="`$REMOTE_ROOT/backups/subscription-rule-mirror.service.bak-rules-`$STAMP"
TIMER_BACKUP="`$REMOTE_ROOT/backups/subscription-rule-mirror.timer.bak-rules-`$STAMP"
mkdir -p "`$REMOTE_ROOT/app" "`$REMOTE_ROOT/backups" "`$REMOTE_ROOT/public/rules"
if [ ! -f "`$REMOTE_ROOT/secrets/hub.runtime.env" ]; then
  echo "ERROR: existing hub runtime env is required for rules-only deployment" >&2
  exit 2
fi
if [ -d "`$REMOTE_ROOT/app" ]; then
  tar -C "`$REMOTE_ROOT" -czf "`$APP_BACKUP" app
fi
if [ -d "`$REMOTE_ROOT/public/rules" ]; then
  tar -C "`$REMOTE_ROOT" -czf "`$RULES_BACKUP" public/rules
fi
if [ -f /etc/systemd/system/subscription-rule-mirror.service ]; then
  cp -p /etc/systemd/system/subscription-rule-mirror.service "`$SERVICE_BACKUP"
fi
if [ -f /etc/systemd/system/subscription-rule-mirror.timer ]; then
  cp -p /etc/systemd/system/subscription-rule-mirror.timer "`$TIMER_BACKUP"
fi

set +e
(
  set -eu
  tar -xzf "`$BUNDLE" -C "`$REMOTE_ROOT" \
    app/rule_mirror.py \
    app/ai_routing.py \
    app/ai-routing-contract.json \
    app/ai-routing-contract.schema.json \
    app/rules.json \
    subscription-rule-mirror.service \
    subscription-rule-mirror.timer
  chmod 700 "`$REMOTE_ROOT/app/rule_mirror.py"
  python3 "`$REMOTE_ROOT/app/rule_mirror.py" --check \
    --registry "`$REMOTE_ROOT/app/rules.json" \
    --contract "`$REMOTE_ROOT/app/ai-routing-contract.json"
  cp -p "`$REMOTE_ROOT/subscription-rule-mirror.service" /etc/systemd/system/subscription-rule-mirror.service
  cp -p "`$REMOTE_ROOT/subscription-rule-mirror.timer" /etc/systemd/system/subscription-rule-mirror.timer
  systemctl daemon-reload
  systemctl enable --now subscription-rule-mirror.timer >/dev/null
  systemctl start subscription-rule-mirror.service
  python3 - "`$REMOTE_ROOT/app/rules.json" "`$REMOTE_ROOT/public/rules/status.json" <<'PY'
import json
from pathlib import Path
import sys

registry = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
status = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
active = [item for item in registry["items"] if item["enabled"]]
expected_ids = {item["id"] for item in active}
summary = status.get("summary", {})
if summary.get("total") != len(active) or summary.get("ok") != len(active):
    raise SystemExit("rules-only status total/ok mismatch")
if summary.get("stale") != 0 or summary.get("failed") != 0:
    raise SystemExit("rules-only status is stale or failed")
items = status.get("items", [])
if len(items) != len(active):
    raise SystemExit("rules-only status item count mismatch")
by_id = {item.get("id"): item for item in items}
if set(by_id) != expected_ids:
    raise SystemExit("rules-only status ids mismatch")
for registry_item in active:
    item = by_id[registry_item["id"]]
    if item.get("status") != "ok":
        raise SystemExit("rules-only item is not ok")
    if registry_item.get("logical_provider"):
        semantic = item.get("semantic", {})
        if semantic.get("status") != "passed":
            raise SystemExit("rules-only logical provider semantic gate failed")
print("rules_only_total=" + str(len(active)))
print("rules_only_semantic=passed")
PY
)
install_rc=`$?
set -e

if [ "`$install_rc" -ne 0 ]; then
  rollback_rc=0
  if [ -f "`$APP_BACKUP" ]; then
    tar -xzf "`$APP_BACKUP" -C "`$REMOTE_ROOT" || rollback_rc=1
  fi
  if [ -f "`$RULES_BACKUP" ]; then
    tar -xzf "`$RULES_BACKUP" -C "`$REMOTE_ROOT" || rollback_rc=1
  fi
  if [ -f "`$SERVICE_BACKUP" ]; then
    cp -p "`$SERVICE_BACKUP" /etc/systemd/system/subscription-rule-mirror.service || rollback_rc=1
  fi
  if [ -f "`$TIMER_BACKUP" ]; then
    cp -p "`$TIMER_BACKUP" /etc/systemd/system/subscription-rule-mirror.timer || rollback_rc=1
  fi
  systemctl daemon-reload || rollback_rc=1
  systemctl start subscription-rule-mirror.service || rollback_rc=1
  if [ "`$rollback_rc" -ne 0 ]; then
    exit 20
  fi
  exit "`$install_rc"
fi
echo "rules_only_installed=ok"
"@

  $runner = New-BoundedRemoteRunner -Body $remoteScript -BundlePath $remoteBundle -ResultPath $remoteResult
  $runnerPath = Join-Path $OutDir 'deploy.sh'
  $bundle = Join-Path $OutDir "subscription-hub-rules-bundle-$runId.tgz"
  Write-Utf8NoBom $runnerPath @($runner)
  try {
    & tar -czf $bundle -C $OutDir `
      deploy.sh `
      app/rule_mirror.py `
      app/ai_routing.py `
      app/ai-routing-contract.json `
      app/ai-routing-contract.schema.json `
      app/rules.json `
      subscription-rule-mirror.service `
      subscription-rule-mirror.timer
    if ($LASTEXITCODE -ne 0) { throw 'local rules-only bundle failed' }
  } finally {
    if (Test-Path -LiteralPath $runnerPath) { Remove-Item -LiteralPath $runnerPath }
  }

  Write-Info 'creating remote rules-only staging'
  & ssh @sshArgs ("mkdir -p " + (Quote-Remote $remoteStage))
  if ($LASTEXITCODE -ne 0) { throw 'ssh mkdir failed' }
  Write-Info 'uploading rules-only bundle'
  & scp @scpArgs $bundle "${SshUser}@${SshHost}:$remoteBundle"
  if ($LASTEXITCODE -ne 0) { throw 'scp rules-only bundle failed' }
  Write-Info 'installing rule mirror runtime inside bounded tmux'
  Invoke-BoundedRemoteBundle -SshArgs $sshArgs -RemoteStage $remoteStage -RunId $runId -DeadlineSeconds $RemoteDeadlineSeconds
  Write-Ok 'rules-only remote install completed; client-facing configuration was not changed'
  exit 0
}

Write-Info 'creating remote staging'
& ssh @sshArgs ("mkdir -p " + (Quote-Remote $remoteStage))
if ($LASTEXITCODE -ne 0) { throw 'ssh mkdir failed' }

if ($IngressKind -eq 'caddy') {
    $remoteScript = @"
set -eu
umask 077
REMOTE_ROOT=$(Quote-Remote $RemoteRoot)
CADDYFILE=$(Quote-Remote $CaddyfilePath)
CONTAINER=$(Quote-Remote $CaddyContainer)
SUBSTORE_CONTAINER=$(Quote-Remote $SubStoreContainer)
HUB_CONTAINER=$(Quote-Remote $HubContainerName)
HUB_NETWORK=$(Quote-Remote $HubDockerNetwork)
HUB_IMAGE=$(Quote-Remote $HubCaddyImage)
PUBLIC_HOST=$(Quote-Remote $PublicHost)
PUBLIC_BASE_URL=$(Quote-Remote $PublicBaseUrl)
PAGE_PATH=$(Quote-Remote $HubPagePath)
LEGACY_REDIRECT_HOSTS=$(Quote-Remote $LegacyRedirectHosts)
STAGE=$(Quote-Remote $remoteStage)
PAYLOAD="`$STAGE/payload"
STAMP=$runId
mkdir -p "`$PAYLOAD"
tar -xzf "`$STAGE/subscription-hub-bundle.tgz" -C "`$PAYLOAD" \
  public app secrets ingress subscription-hub.service subscription-rule-mirror.service subscription-rule-mirror.timer
mkdir -p "`$REMOTE_ROOT/public" "`$REMOTE_ROOT/secrets" "`$REMOTE_ROOT/ingress" "`$REMOTE_ROOT/app" "`$REMOTE_ROOT/backups"
if [ -f "`$CADDYFILE" ]; then
  cp -p "`$CADDYFILE" "`$REMOTE_ROOT/backups/Caddyfile.bak-`$STAMP"
fi
if [ -d "`$REMOTE_ROOT/public" ]; then
  tar -C "`$REMOTE_ROOT" -czf "`$REMOTE_ROOT/backups/public.bak-`$STAMP.tgz" public
fi
cp -p "`$PAYLOAD"/public/index.html "`$REMOTE_ROOT/public/index.html"
cp -p "`$PAYLOAD"/public/links.json "`$REMOTE_ROOT/public/links.json"
cp -p "`$PAYLOAD"/public/status.json "`$REMOTE_ROOT/public/status.json"
cp -p "`$PAYLOAD"/public/shadowrocket.conf "`$REMOTE_ROOT/public/shadowrocket.conf"
cp -p "`$PAYLOAD"/public/qr-*.svg "`$REMOTE_ROOT/public/"
mkdir -p "`$REMOTE_ROOT/public/rules"
cp -p "`$PAYLOAD"/public/rules/status.json "`$REMOTE_ROOT/public/rules/status.json"
cp -p "`$PAYLOAD"/app/server.py "`$REMOTE_ROOT/app/server.py"
cp -p "`$PAYLOAD"/app/rule_mirror.py "`$REMOTE_ROOT/app/rule_mirror.py"
cp -p "`$PAYLOAD"/app/ai_routing.py "`$REMOTE_ROOT/app/ai_routing.py"
cp -p "`$PAYLOAD"/app/ai-routing-contract.json "`$REMOTE_ROOT/app/ai-routing-contract.json"
cp -p "`$PAYLOAD"/app/ai-routing-contract.schema.json "`$REMOTE_ROOT/app/ai-routing-contract.schema.json"
cp -p "`$PAYLOAD"/app/rules.json "`$REMOTE_ROOT/app/rules.json"
chmod 700 "`$REMOTE_ROOT/app/server.py"
chmod 700 "`$REMOTE_ROOT/app/rule_mirror.py"
BACKEND_PATH="`$(docker inspect "`$SUBSTORE_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' | awk -F= '/SUB_STORE_FRONTEND_BACKEND_PATH|SUB_STORE_BACKEND_PATH/{print `$2; exit}')"
if [ -z "`$BACKEND_PATH" ]; then
  echo "ERROR: Sub-Store backend path not found" >&2
  exit 2
fi
python3 - "`$PAYLOAD"/secrets/hub.runtime.env "`$REMOTE_ROOT/secrets/hub.runtime.env" "`$BACKEND_PATH" <<'PY'
from pathlib import Path
import sys
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
backend = sys.argv[3]
lines = []
seen = False
for line in src.read_text(encoding='utf-8').splitlines():
    if line.startswith('SUBSTORE_BACKEND_PATH='):
        lines.append('SUBSTORE_BACKEND_PATH=' + backend)
        seen = True
    else:
        lines.append(line)
if not seen:
    lines.append('SUBSTORE_BACKEND_PATH=' + backend)
text = '\n'.join(lines) + '\n'
if '<' in text or '>' in text:
    raise SystemExit('runtime env still contains placeholder text')
dst.write_text(text, encoding='utf-8')
PY
chmod 600 "`$REMOTE_ROOT/secrets/hub.runtime.env"
if [ -f "`$PAYLOAD"/secrets/Caddyfile.auth ]; then
  cp -p "`$PAYLOAD"/secrets/Caddyfile.auth "`$REMOTE_ROOT/secrets/Caddyfile.auth"
  chmod 600 "`$REMOTE_ROOT/secrets/Caddyfile.auth"
fi
cp -p "`$PAYLOAD"/ingress/subscription-hub.Caddyfile "`$REMOTE_ROOT/ingress/subscription-hub.Caddyfile"
cp -p "`$PAYLOAD"/ingress/internal.Caddyfile "`$REMOTE_ROOT/ingress/internal.Caddyfile"
cp -p "`$PAYLOAD"/subscription-rule-mirror.service /etc/systemd/system/subscription-rule-mirror.service
cp -p "`$PAYLOAD"/subscription-rule-mirror.timer /etc/systemd/system/subscription-rule-mirror.timer
USER_VALUE="`$(awk -F= '/^HUB_BASIC_AUTH_USER=/{print `$2; exit}' "`$REMOTE_ROOT/secrets/hub.runtime.env")"
PASS_VALUE="`$(awk -F= '/^HUB_BASIC_AUTH_PASSWORD=/{print `$2; exit}' "`$REMOTE_ROOT/secrets/hub.runtime.env")"
if [ -z "`$USER_VALUE" ] || [ -z "`$PASS_VALUE" ]; then
  echo "ERROR: Basic Auth runtime values missing" >&2
  exit 5
fi
HASH_VALUE="`$(docker run --rm "`$HUB_IMAGE" caddy hash-password --plaintext "`$PASS_VALUE")"
cat > "`$REMOTE_ROOT/secrets/caddy.env" <<EOF
HUB_BASIC_AUTH_USER=`$USER_VALUE
HUB_BASIC_AUTH_HASH=`$HASH_VALUE
SUBSTORE_BACKEND_PATH=`$BACKEND_PATH
EOF
chmod 600 "`$REMOTE_ROOT/secrets/caddy.env"
systemctl disable --now subscription-hub.service >/dev/null 2>&1 || true
docker rm -f "`$HUB_CONTAINER" >/dev/null 2>&1 || true
docker run -d \
  --name "`$HUB_CONTAINER" \
  --restart unless-stopped \
  --network "`$HUB_NETWORK" \
  --env-file "`$REMOTE_ROOT/secrets/caddy.env" \
  -v "`$REMOTE_ROOT/public:/srv/public:ro" \
  -v "`$REMOTE_ROOT/ingress/internal.Caddyfile:/etc/caddy/Caddyfile:ro" \
  "`$HUB_IMAGE" >/dev/null
systemctl daemon-reload
systemctl enable --now subscription-rule-mirror.timer >/dev/null
systemctl start subscription-rule-mirror.service
python3 - "`$CADDYFILE" "`$REMOTE_ROOT/ingress/subscription-hub.Caddyfile" "`$PUBLIC_BASE_URL" "`$PAGE_PATH" "`$LEGACY_REDIRECT_HOSTS" <<'PY'
from pathlib import Path
import sys
conf = Path(sys.argv[1])
frag = Path(sys.argv[2]).read_text(encoding='utf-8')
public_base = sys.argv[3].rstrip('/')
page_path = sys.argv[4].rstrip('/')
legacy_hosts = [part.strip().lower() for part in sys.argv[5].replace(',', ' ').split() if part.strip()]
text = conf.read_text(encoding='utf-8') if conf.exists() else ''

def remove_blocks(value, start, end):
    removed = 0
    while start in value and end in value:
        before, rest = value.split(start, 1)
        _, after = rest.split(end, 1)
        value = before.rstrip() + '\n' + after.lstrip()
        removed += 1
    return value, removed

text, managed_removed = remove_blocks(
    text,
    '# BEGIN subscription-hub managed block',
    '# END subscription-hub managed block',
)
text, legacy_removed = remove_blocks(
    text,
    '# BEGIN subscription-hub legacy redirect block',
    '# END subscription-hub legacy redirect block',
)
text = (text.rstrip() + '\n\n' + frag.rstrip() + '\n') if text.strip() else (frag.rstrip() + '\n')

if legacy_hosts:
    lines = text.splitlines(keepends=True)
    inserts = []
    matched_hosts = set()
    depth = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        before_depth = depth
        if before_depth == 0 and stripped.endswith('{') and not stripped.startswith('{'):
            label = stripped.split('{', 1)[0].strip()
            labels = [part.strip().rstrip(',').lower() for part in label.split(',')]
            matched = [host for host in legacy_hosts if host in labels]
            if matched:
                matched_hosts.update(matched)
                close = None
                local_depth = 0
                for j in range(i, len(lines)):
                    before_local = local_depth
                    local_depth += lines[j].count('{') - lines[j].count('}')
                    if j > i and before_local > 0 and local_depth == 0:
                        close = j
                        break
                if close is None:
                    raise SystemExit('legacy redirect target site block is not closed')
                insert_at = close
                rel_depth = 0
                for j in range(i + 1, close):
                    s = lines[j].strip()
                    if rel_depth == 0 and (
                        s.startswith('@')
                        or s.startswith('handle')
                        or s.startswith('route')
                        or s.startswith('respond')
                        or s.startswith('redir')
                        or s.startswith('reverse_proxy')
                        or s.startswith('file_server')
                        or s.startswith('root ')
                    ):
                        insert_at = j
                        break
                    rel_depth += lines[j].count('{') - lines[j].count('}')
                block = (
                    '    # BEGIN subscription-hub legacy redirect block\n'
                    f'    @subscriptionHubLegacyExact path {page_path}\n'
                    '    handle @subscriptionHubLegacyExact {\n'
                    f'        redir {public_base}{page_path}/ 302\n'
                    '    }\n'
                    f'    @subscriptionHubLegacyTree path {page_path}/*\n'
                    '    handle @subscriptionHubLegacyTree {\n'
                    f'        redir {public_base}{{uri}} 302\n'
                    '    }\n'
                    '    # END subscription-hub legacy redirect block\n\n'
                )
                inserts.append((insert_at, block))
        depth += line.count('{') - line.count('}')
    missing = [host for host in legacy_hosts if host not in matched_hosts]
    if missing:
        raise SystemExit('legacy redirect host site block was not found')
    for insert_at, block in sorted(inserts, key=lambda item: item[0], reverse=True):
        lines.insert(insert_at, block)
    text = ''.join(lines)

conf.write_text(text, encoding='utf-8')
print('caddy_mode=standalone-site')
print('managed_blocks_removed=' + str(managed_removed))
print('legacy_blocks_removed=' + str(legacy_removed))
print('legacy_redirect_hosts=' + str(len(legacy_hosts)))
PY
CODE=
for i in 1 2 3 4 5; do
  if docker exec "`$CONTAINER" wget -q -O /dev/null "http://`$HUB_CONTAINER:19180/healthz"; then
    CODE=200
    echo "local_hub=200"
    break
  fi
  sleep 1
done
if [ "`${CODE:-}" != "200" ]; then
  echo "ERROR: local hub health check failed" >&2
  exit 4
fi
if ! docker exec "`$CONTAINER" caddy validate --config /etc/caddy/Caddyfile >/dev/null; then
  cp -p "`$REMOTE_ROOT/backups/Caddyfile.bak-`$STAMP" "`$CADDYFILE"
  echo "ERROR: Caddy validate failed; restored backup" >&2
  exit 3
fi
docker exec "`$CONTAINER" caddy reload --config /etc/caddy/Caddyfile >/dev/null
echo "backup_dir=`$REMOTE_ROOT/backups"
mv "`$PAYLOAD" "`$REMOTE_ROOT/backups/deploy-payload-`$STAMP"
echo "installed=ok"
"@
} else {
    $remoteScript = @"
set -eu
umask 077
REMOTE_ROOT=$(Quote-Remote $RemoteRoot)
CONF_PATH=$(Quote-Remote $OpenRestyConfPath)
CONTAINER=$(Quote-Remote $OpenRestyContainer)
STAGE=$(Quote-Remote $remoteStage)
PAYLOAD="`$STAGE/payload"
STAMP=$runId
mkdir -p "`$PAYLOAD"
tar -xzf "`$STAGE/subscription-hub-bundle.tgz" -C "`$PAYLOAD" \
  public app secrets ingress subscription-hub.service subscription-rule-mirror.service subscription-rule-mirror.timer
mkdir -p "`$REMOTE_ROOT/public" "`$REMOTE_ROOT/secrets" "`$REMOTE_ROOT/ingress" "`$REMOTE_ROOT/app" "`$REMOTE_ROOT/backups"
if [ -f "`$CONF_PATH" ]; then
  cp -p "`$CONF_PATH" "`$REMOTE_ROOT/backups/site.conf.bak-`$STAMP"
fi
if [ -d "`$REMOTE_ROOT/public" ]; then
  tar -C "`$REMOTE_ROOT" -czf "`$REMOTE_ROOT/backups/public.bak-`$STAMP.tgz" public
fi
cp -p "`$PAYLOAD"/public/index.html "`$REMOTE_ROOT/public/index.html"
cp -p "`$PAYLOAD"/public/links.json "`$REMOTE_ROOT/public/links.json"
cp -p "`$PAYLOAD"/public/status.json "`$REMOTE_ROOT/public/status.json"
cp -p "`$PAYLOAD"/public/shadowrocket.conf "`$REMOTE_ROOT/public/shadowrocket.conf"
cp -p "`$PAYLOAD"/public/qr-*.svg "`$REMOTE_ROOT/public/"
mkdir -p "`$REMOTE_ROOT/public/rules"
cp -p "`$PAYLOAD"/public/rules/status.json "`$REMOTE_ROOT/public/rules/status.json"
cp -p "`$PAYLOAD"/app/rule_mirror.py "`$REMOTE_ROOT/app/rule_mirror.py"
cp -p "`$PAYLOAD"/app/ai_routing.py "`$REMOTE_ROOT/app/ai_routing.py"
cp -p "`$PAYLOAD"/app/ai-routing-contract.json "`$REMOTE_ROOT/app/ai-routing-contract.json"
cp -p "`$PAYLOAD"/app/ai-routing-contract.schema.json "`$REMOTE_ROOT/app/ai-routing-contract.schema.json"
cp -p "`$PAYLOAD"/app/rules.json "`$REMOTE_ROOT/app/rules.json"
chmod 700 "`$REMOTE_ROOT/app/rule_mirror.py"
cp -p "`$PAYLOAD"/subscription-rule-mirror.service /etc/systemd/system/subscription-rule-mirror.service
cp -p "`$PAYLOAD"/subscription-rule-mirror.timer /etc/systemd/system/subscription-rule-mirror.timer
cp -p "`$PAYLOAD"/secrets/htpasswd "`$REMOTE_ROOT/secrets/htpasswd"
chmod 600 "`$REMOTE_ROOT/secrets/htpasswd"
cp -p "`$PAYLOAD"/ingress/subscription-hub.nginx.conf "`$REMOTE_ROOT/ingress/subscription-hub.nginx.conf"
systemctl daemon-reload
systemctl enable --now subscription-rule-mirror.timer >/dev/null
systemctl start subscription-rule-mirror.service
python3 - "`$CONF_PATH" "`$REMOTE_ROOT/ingress/subscription-hub.nginx.conf" <<'PY'
from pathlib import Path
import sys
conf = Path(sys.argv[1])
frag = Path(sys.argv[2]).read_text(encoding='utf-8')
text = conf.read_text(encoding='utf-8') if conf.exists() else ''
start = '# BEGIN subscription-hub managed block'
end = '# END subscription-hub managed block'
if start in text and end in text:
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    text = before.rstrip() + '\n\n' + frag.rstrip() + '\n' + after
else:
    insert = text.rstrip() + '\n\n' + frag if text.strip() else frag
    text = insert
conf.write_text(text, encoding='utf-8')
PY
docker exec "`$CONTAINER" openresty -t >/dev/null
docker exec "`$CONTAINER" openresty -s reload
echo "backup_dir=`$REMOTE_ROOT/backups"
mv "`$PAYLOAD" "`$REMOTE_ROOT/backups/deploy-payload-`$STAMP"
echo "installed=ok"
"@
}

$remoteBundle = "$remoteStage/subscription-hub-bundle.tgz"
$remoteResult = "$remoteStage/result.env"
$runner = New-BoundedRemoteRunner -Body $remoteScript -BundlePath $remoteBundle -ResultPath $remoteResult
$runnerPath = Join-Path $OutDir 'deploy.sh'
$bundle = Join-Path $OutDir "subscription-hub-bundle-$runId.tgz"
$runtimeEnvCopy = Join-Path $OutDir 'secrets\hub.runtime.env'
Copy-Item -LiteralPath $RenderEnvFile -Destination $runtimeEnvCopy -Force
Write-Utf8NoBom $runnerPath @($runner)
try {
  & tar -czf $bundle -C $OutDir `
    deploy.sh `
    public `
    app `
    secrets `
    ingress `
    subscription-hub.service `
    subscription-rule-mirror.service `
    subscription-rule-mirror.timer
  if ($LASTEXITCODE -ne 0) { throw 'local tar bundle failed' }
} finally {
  if (Test-Path -LiteralPath $runnerPath) { Remove-Item -LiteralPath $runnerPath }
}

Write-Info 'uploading rendered artifacts'
& scp @scpArgs $bundle "${SshUser}@${SshHost}:$remoteBundle"
if ($LASTEXITCODE -ne 0) { throw 'scp bundle failed' }
Write-Info 'installing hub inside bounded tmux'
Invoke-BoundedRemoteBundle -SshArgs $sshArgs -RemoteStage $remoteStage -RunId $runId -DeadlineSeconds $RemoteDeadlineSeconds
Write-Ok 'remote install completed'
