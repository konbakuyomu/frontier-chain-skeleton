[CmdletBinding()]
param(
    [string]$SshHost = "sjc-snap",
    [int]$SshPort = 14514,
    [string]$SshUser = "root",
    [string]$SshKey = "",
    [string]$RemoteDir = "/opt/codex-stacks/att-proxy-share",
    [switch]$Apply,
    [switch]$BootstrapCertbot,
    [switch]$SkipLocalChecks
)

$ErrorActionPreference = "Stop"
$SourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = "$SshUser@$SshHost"
$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss") + "-$PID"
$RemoteStage = "$RemoteDir/.stage-$RunId"
$RemoteBackup = "$RemoteDir/.deploy-backups/$RunId"
$RemoteRuntime = "$RemoteDir/.runtime"
$RemoteBackupRuntime = "$RemoteBackup/runtime"

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$File failed with exit code $LASTEXITCODE"
    }
}

function Quote-Posix {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "'\''") + "'"
}

$SshArgs = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=12",
    "-p", "$SshPort"
)
if ($SshKey) {
    $SshArgs += @("-i", $SshKey)
}

function Invoke-Remote {
    param([Parameter(Mandatory = $true)][string]$Command)
    Invoke-Native -File "ssh" -Arguments ($SshArgs + @($Target, $Command))
}

$Allowlist = @(
    "manage.py",
    "compose.yaml",
    "README.md",
    "tests/test_manage.py",
    "systemd/att-proxy-share-certbot-renew.service",
    "systemd/att-proxy-share-certbot-renew.timer",
    "templates/certbot-http01.caddyfile"
)
foreach ($Name in $Allowlist) {
    if (-not (Test-Path (Join-Path $SourceDir $Name) -PathType Leaf)) {
        throw "missing deploy file: $Name"
    }
}

if (-not $SkipLocalChecks) {
    Push-Location $SourceDir
    try {
        Invoke-Native -File "python" -Arguments @("-m", "py_compile", "manage.py")
        if (Test-Path "tests" -PathType Container) {
            Invoke-Native -File "python" -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")
        }
        Invoke-Native -File "docker" -Arguments @("compose", "--profile", "certbot", "-f", "compose.yaml", "config")
    }
    finally {
        Pop-Location
    }
}

Write-Host "ATT proxy deployment plan"
Write-Host "  target: $Target"
Write-Host "  remote directory: $RemoteDir"
Write-Host "  mihomo image: metacubex/mihomo:v1.19.29"
Write-Host "  certbot image: certbot/certbot:v5.7.0@sha256:d07bd043d61d6bee1114235ac12c2e9a5c54b6931b3ccf5e1174d6c8c4afaa95"
Write-Host "  certbot network: edge_ingress (no host-published HTTP-01 port)"
Write-Host "  runtime upload: none (.runtime is preserved on the server)"
Write-Host "  bootstrap certbot only: $($BootstrapCertbot.IsPresent)"

if (-not $Apply) {
    Write-Host "Dry-run only. Re-run with -Apply after reviewing the plan."
    exit 0
}

# Keep a root-only backup of the exact files this deploy may replace. Legacy runtime
# files are backed up before manage.py performs its one-time .runtime migration.
$RemoteBackupQ = Quote-Posix $RemoteBackup
$RemoteDirQ = Quote-Posix $RemoteDir
$RemoteStageQ = Quote-Posix $RemoteStage
$RemoteRuntimeQ = Quote-Posix $RemoteRuntime
$RemoteBackupRuntimeQ = Quote-Posix $RemoteBackupRuntime
Invoke-Remote -Command @"
set -eu
test -d $RemoteDirQ
umask 077
install -d -m 700 $RemoteBackupQ $RemoteBackupRuntimeQ $RemoteStageQ
install -d -m 700 $RemoteStageQ/tests $RemoteStageQ/systemd $RemoteStageQ/templates
for name in manage.py compose.yaml README.md users.json config.json public-host; do
  if [ -e $RemoteDirQ/`$name ]; then
    install -m 600 $RemoteDirQ/`$name $RemoteBackupQ/`$name
  fi
done
for name in users.json config.json public-host; do
  if [ -e $RemoteRuntimeQ/`$name ]; then
    install -m 600 $RemoteRuntimeQ/`$name $RemoteBackupRuntimeQ/`$name
  fi
done
if [ -e $RemoteDirQ/systemd/att-proxy-share-certbot-renew.service ]; then
  install -d -m 700 $RemoteBackupQ/systemd
  install -m 600 $RemoteDirQ/systemd/att-proxy-share-certbot-renew.service $RemoteBackupQ/systemd/att-proxy-share-certbot-renew.service
fi
if [ -e $RemoteDirQ/systemd/att-proxy-share-certbot-renew.timer ]; then
  install -d -m 700 $RemoteBackupQ/systemd
  install -m 600 $RemoteDirQ/systemd/att-proxy-share-certbot-renew.timer $RemoteBackupQ/systemd/att-proxy-share-certbot-renew.timer
fi
if [ -e $RemoteDirQ/templates/certbot-http01.caddyfile ]; then
  install -d -m 700 $RemoteBackupQ/templates
  install -m 600 $RemoteDirQ/templates/certbot-http01.caddyfile $RemoteBackupQ/templates/certbot-http01.caddyfile
fi
if [ -e $RemoteDirQ/tests/test_manage.py ]; then
  install -d -m 700 $RemoteBackupQ/tests
  install -m 600 $RemoteDirQ/tests/test_manage.py $RemoteBackupQ/tests/test_manage.py
fi
if [ -d $RemoteRuntimeQ/certbot ]; then
  tar -C $RemoteRuntimeQ -cf $RemoteBackupQ/certbot-runtime.tar certbot
  chmod 600 $RemoteBackupQ/certbot-runtime.tar
fi
if docker inspect att-proxy-share > $RemoteBackupQ/container-inspect.json 2>/dev/null; then
  chmod 600 $RemoteBackupQ/container-inspect.json
fi
ss -H -lntup > $RemoteBackupQ/listeners-before.txt 2>/dev/null || true
chmod 600 $RemoteBackupQ/listeners-before.txt
if command -v ufw >/dev/null 2>&1; then
  ufw status numbered > $RemoteBackupQ/ufw-before.txt || true
  chmod 600 $RemoteBackupQ/ufw-before.txt
fi
docker network inspect edge_ingress >/dev/null
"@

$ScpArgs = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=12",
    "-P", "$SshPort"
)
if ($SshKey) {
    $ScpArgs += @("-i", $SshKey)
}
foreach ($Name in $Allowlist) {
    $RemoteFile = "${Target}:$RemoteStage/$Name"
    Invoke-Native -File "scp" -Arguments ($ScpArgs + @((Join-Path $SourceDir $Name), $RemoteFile))
}

Invoke-Remote -Command @"
set -eu
cd $RemoteStageQ
PYTHONDONTWRITEBYTECODE=1 python3 -c 'import ast; ast.parse(open("manage.py", encoding="utf-8").read(), filename="manage.py")'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
docker compose --profile certbot -f compose.yaml config >/dev/null
"@

$RemoteManageQ = Quote-Posix "$RemoteDir/manage.py"
$RemoteComposeQ = Quote-Posix "$RemoteDir/compose.yaml"
$RemoteReadmeQ = Quote-Posix "$RemoteDir/README.md"
try {
    Invoke-Remote -Command @"
set -eu
install -m 700 -d $RemoteDirQ $RemoteDirQ/tests $RemoteDirQ/systemd $RemoteDirQ/templates
mv -f $RemoteStageQ/manage.py $RemoteManageQ
mv -f $RemoteStageQ/compose.yaml $RemoteComposeQ
mv -f $RemoteStageQ/README.md $RemoteReadmeQ
mv -f $RemoteStageQ/tests/test_manage.py $RemoteDirQ/tests/test_manage.py
mv -f $RemoteStageQ/systemd/att-proxy-share-certbot-renew.service $RemoteDirQ/systemd/att-proxy-share-certbot-renew.service
mv -f $RemoteStageQ/systemd/att-proxy-share-certbot-renew.timer $RemoteDirQ/systemd/att-proxy-share-certbot-renew.timer
mv -f $RemoteStageQ/templates/certbot-http01.caddyfile $RemoteDirQ/templates/certbot-http01.caddyfile
rmdir $RemoteStageQ/tests
rmdir $RemoteStageQ/systemd
rmdir $RemoteStageQ/templates
rmdir $RemoteStageQ
"@
    if ($BootstrapCertbot) {
        Invoke-Remote -Command @"
set -eu
cd $RemoteDirQ
PYTHONDONTWRITEBYTECODE=1 python3 manage.py prepare-certbot
"@
        Write-Host "Certbot bootstrap source is staged. Install and validate the narrow Caddy HTTP-01 route, issue TLS, then run reconcile."
    }
    else {
        Invoke-Remote -Command @"
set -eu
cd $RemoteDirQ
PYTHONDONTWRITEBYTECODE=1 python3 manage.py check-tls
PYTHONDONTWRITEBYTECODE=1 python3 manage.py reconcile
docker compose -f compose.yaml ps
expected_pid=`$(docker inspect -f '{{.State.Pid}}' att-proxy-share)
case "`$expected_pid" in
  ''|0|*[!0-9]*) echo 'att-proxy-share has no running container PID' >&2; exit 1 ;;
esac
for port in 34567 8443; do
  if ! ss -H -lntp "( sport = :`$port )" | grep -Eq "pid=`$expected_pid,"; then
    echo "att-proxy-share does not own expected TCP port `$port" >&2
    exit 1
  fi
  if ss -H -lnup "( sport = :`$port )" | grep -q .; then
    echo "unexpected UDP listener on proxy port `$port" >&2
    exit 1
  fi
done
if ss -H -lntp "( sport = :18081 )" | grep -q .; then
  echo 'unexpected host listener on internal Certbot HTTP-01 port 18081' >&2
  exit 1
fi
"@
    }
}
catch {
    $deployFailure = $_
    Write-Warning "Deployment failed; attempting source and runtime rollback from $RemoteBackup."
    try {
        Invoke-Remote -Command @"
set -eu
test -d $RemoteBackupQ
if [ -e $RemoteBackupQ/manage.py ]; then
  for name in manage.py compose.yaml README.md; do
    if [ -e $RemoteBackupQ/`$name ]; then
      install -m 600 $RemoteBackupQ/`$name $RemoteDirQ/`$name
    fi
  done
  if [ -e $RemoteBackupQ/systemd/att-proxy-share-certbot-renew.service ]; then
    install -d -m 700 $RemoteDirQ/systemd
    install -m 600 $RemoteBackupQ/systemd/att-proxy-share-certbot-renew.service $RemoteDirQ/systemd/att-proxy-share-certbot-renew.service
  fi
  if [ -e $RemoteBackupQ/systemd/att-proxy-share-certbot-renew.timer ]; then
    install -d -m 700 $RemoteDirQ/systemd
    install -m 600 $RemoteBackupQ/systemd/att-proxy-share-certbot-renew.timer $RemoteDirQ/systemd/att-proxy-share-certbot-renew.timer
  fi
  if [ -e $RemoteBackupQ/templates/certbot-http01.caddyfile ]; then
    install -d -m 700 $RemoteDirQ/templates
    install -m 600 $RemoteBackupQ/templates/certbot-http01.caddyfile $RemoteDirQ/templates/certbot-http01.caddyfile
  fi
  if [ -e $RemoteBackupQ/tests/test_manage.py ]; then
    install -d -m 700 $RemoteDirQ/tests
    install -m 600 $RemoteBackupQ/tests/test_manage.py $RemoteDirQ/tests/test_manage.py
  fi
  install -d -m 700 $RemoteRuntimeQ
  for name in users.json config.json public-host; do
    if [ -e $RemoteBackupRuntimeQ/`$name ]; then
      install -m 600 $RemoteBackupRuntimeQ/`$name $RemoteRuntimeQ/`$name
    fi
  done
  cd $RemoteDirQ
  PYTHONDONTWRITEBYTECODE=1 python3 manage.py reconcile || docker compose -f compose.yaml stop att-proxy-share || true
else
  docker compose -f $RemoteComposeQ stop att-proxy-share || true
fi
"@
    }
    catch {
        Write-Warning "Automatic rollback did not complete; inspect the root-only backup at $RemoteBackup."
    }
    throw $deployFailure
}

Write-Host "ATT proxy deployment complete. Backup: $RemoteBackup"
