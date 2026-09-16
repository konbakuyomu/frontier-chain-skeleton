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
  [string]$SubStoreDataPath = '',
  [string]$ContainerName = 'frontier-sub-store',
  [string]$CollectionName = 'merged-airports',
  [string]$MihomoFileName = 'frontier-chain-mihomo',
  [string]$IosAirportsCollection = 'ios-airports-uri',
  [string]$IosHy2Collection = 'ios-evoxt-hy2-shadowrocket',
  [string]$LocalBaseUrl = 'http://127.0.0.1:3001',
  [string]$ExpectedBackendPath = $env:FRONTIER_EXPECTED_BACKEND_PATH,
  [int]$ExpectedThreeXVless = -1,
  [int]$ExpectedThreeXHy2 = -1,
  [int]$ExpectedEdgeUsV2Vmess = -1,
  [int]$ExpectedEdgeUsV2Hy2 = -1,
  [int]$MinIosOrdinaryNodes = 1,
  [int]$MinIosHy2Nodes = 0
)

$ErrorActionPreference = 'Stop'

if (-not $SshUser) { $SshUser = 'root' }
if (-not $SubStoreDataPath) { $SubStoreDataPath = "$SubStoreDir/data/sub-store.json" }

$RepoRoot = Split-Path -Parent $PSScriptRoot
$JsFiles = @(
  (Join-Path $RepoRoot 'substore-source-marker.js'),
  (Join-Path $RepoRoot 'shadowrocket-nodes-injector.js'),
  (Join-Path $RepoRoot 'main.js')
)
$ShadowrocketConfig = Join-Path $RepoRoot 'shadowrocket.conf'
$PyFiles = @(
  (Join-Path $PSScriptRoot 'remote-verify-substore.py'),
  (Join-Path $PSScriptRoot 'remote-apply-substore.py'),
  (Join-Path $PSScriptRoot 'update-powerfullz-inline.py'),
  (Join-Path $RepoRoot 'subscription-hub\ai_routing.py'),
  (Join-Path $RepoRoot 'subscription-hub\rule_mirror.py'),
  (Join-Path $RepoRoot 'subscription-hub\render.py'),
  (Join-Path $PSScriptRoot 'render-ai-routing.py')
)
$NodeCheckScripts = @(
  (Join-Path $PSScriptRoot 'check-3x-injector.js'),
  (Join-Path $PSScriptRoot 'check-ai-routing.js')
)

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

function Get-SshArgs {
  $args = @()
  if ($SshKey) { $args += @('-i', $SshKey) }
  if ($SshPort) { $args += @('-p', $SshPort) }
  $args += @('-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new', "$SshUser@$SshHost")
  return $args
}

function Get-ScpArgs {
  $args = @()
  if ($SshKey) { $args += @('-i', $SshKey) }
  if ($SshPort) { $args += @('-P', $SshPort) }
  $args += @('-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new')
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
  foreach ($file in $NodeCheckScripts) {
    & node $file
    if ($LASTEXITCODE -ne 0) { throw "node behavior check failed: $file" }
    Write-Ok "node behavior check passed: $(Split-Path -Leaf $file)"
  }
} else {
  Write-Warn2 'node not found, skipped JS syntax checks'
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
  & python -B -c "import pathlib,sys; [compile(pathlib.Path(p).read_text(encoding='utf-8'), p, 'exec') for p in sys.argv[1:]]" @PyFiles
  if ($LASTEXITCODE -ne 0) { throw 'python syntax check failed' }
  Write-Ok 'python syntax checks passed'
  & python (Join-Path $RepoRoot 'subscription-hub\rule_mirror.py') --check --registry (Join-Path $RepoRoot 'subscription-hub\rules.json') --contract (Join-Path $RepoRoot 'ai-routing-contract.json')
  if ($LASTEXITCODE -ne 0) { throw 'AI rule registry contract check failed' }
  & python (Join-Path $PSScriptRoot 'render-ai-routing.py') --check
  if ($LASTEXITCODE -ne 0) { throw 'AI routing renderer drift check failed' }
  & python -B -m unittest discover -s (Join-Path $RepoRoot 'subscription-hub\tests') -p 'test_ai_routing.py'
  if ($LASTEXITCODE -ne 0) { throw 'AI routing regression tests failed' }
  Write-Ok 'AI routing contract, generated blocks, and regression tests passed'
} else {
  Write-Warn2 'python not found locally, skipped Python syntax checks'
}

$srText = Get-Content -LiteralPath $ShadowrocketConfig -Raw -Encoding UTF8
if ($srText -match 'frontier-chain-skeleton@main') {
  throw 'shadowrocket.conf must not reference frontier-chain-skeleton@main; use commit-pinned jsDelivr URLs or inline own rules'
}
if ($srText -match 'ai-extensions\.list') {
  throw 'shadowrocket.conf must inline own AI extension rules instead of loading ai-extensions.list through a cached branch URL'
}
if ($srText -notmatch '\u8282\u70b9\u9009\u62e9[^\r\n]*\u5bb6\u5bbd\u9009\u62e9') {
  throw 'shadowrocket.conf primary selector does not expose stable residential selector'
}
if ($srText -notmatch '\u5bb6\u5bbd\u9009\u62e9[^\r\n]*\u7f8e\u56fd\u5bb6\u5bbd' -or $srText -notmatch '\u5bb6\u5bbd\u9009\u62e9[^\r\n]*\u4e9a\u592a\u5bb6\u5bbd') {
  throw 'shadowrocket.conf residential selector missing US/APAC residential shortcuts'
}
if ($srText -notmatch 'AI \u670d\u52a1[^\r\n]*policy-select-name=.*\u5bb6\u5bbd\u9009\u62e9' -or $srText -notmatch 'PayPal[^\r\n]*policy-select-name=.*\u5bb6\u5bbd\u9009\u62e9') {
  throw 'shadowrocket.conf AI/PayPal groups must default to the stable residential selector'
}
if ($srText -notmatch 'DOMAIN,cloudcode-pa\.googleapis\.com,' -or $srText -notmatch 'DOMAIN,cloudaicompanion\.googleapis\.com,') {
  throw 'shadowrocket.conf missing inline Gemini CLI compatibility rules'
}
foreach ($sentinel in @('DOMAIN,api\.openai\.com,', 'DOMAIN,api\.anthropic\.com,', 'DOMAIN,api\.x\.ai,', 'DOMAIN,grok\.x\.com,')) {
  if ($srText -notmatch $sentinel) { throw "shadowrocket.conf missing AI routing sentinel: $sentinel" }
}
if ($srText -match 'DOMAIN-SUFFIX,x\.com,🤖 AI 服务' -or $srText -match 'shadowrocket-ai\.list') {
  throw 'shadowrocket.conf retains an unsafe broad X rule or legacy opaque AI mirror'
}
if ($srText -notmatch 'shadowrocket-advertising-domain\.list,🛑 广告拦截' -or $srText -notmatch 'shadowrocket-advertising\.list,🛑 广告拦截') {
  throw 'shadowrocket.conf lost the non-AI advertising rule block while rendering AI rules'
}
Write-Ok 'shadowrocket.conf local checks passed'

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
  $remoteAiContract = "${SshUser}@${SshHost}:$remoteStage/ai-routing-contract.json"
  & scp @scpArgs (Join-Path $RepoRoot 'ai-routing-contract.json') $remoteAiContract
  if ($LASTEXITCODE -ne 0) { throw 'scp AI routing contract failed' }
  $remoteAiRegistry = "${SshUser}@${SshHost}:$remoteStage/ai-routing-rules.json"
  & scp @scpArgs (Join-Path $RepoRoot 'subscription-hub\rules.json') $remoteAiRegistry
  if ($LASTEXITCODE -ne 0) { throw 'scp AI routing registry failed' }

  $cmd = @(
    'python3',
    (Quote-Remote "$remoteStage/remote-verify-substore.py"),
    '--app-dir', (Quote-Remote $SubStoreDir),
    '--data', (Quote-Remote $SubStoreDataPath),
    '--container', (Quote-Remote $ContainerName),
    '--collection', (Quote-Remote $CollectionName),
    '--file', (Quote-Remote $MihomoFileName),
    '--ios-airports-collection', (Quote-Remote $IosAirportsCollection),
    '--ios-hy2-collection', (Quote-Remote $IosHy2Collection),
    '--local-base-url', (Quote-Remote $LocalBaseUrl),
    '--min-ios-ordinary-nodes', $MinIosOrdinaryNodes,
    '--min-ios-hy2-nodes', $MinIosHy2Nodes,
    '--ai-routing-contract', (Quote-Remote "$remoteStage/ai-routing-contract.json"),
    '--ai-routing-registry', (Quote-Remote "$remoteStage/ai-routing-rules.json")
  )
  if ($ExpectedBackendPath) {
    $cmd += @('--expected-backend-path', (Quote-Remote $ExpectedBackendPath))
  }
  if ($ExpectedThreeXVless -ge 0) {
    $cmd += @('--expected-three-x-vless', $ExpectedThreeXVless)
  }
  if ($ExpectedThreeXHy2 -ge 0) {
    $cmd += @('--expected-three-x-hy2', $ExpectedThreeXHy2)
  }
  if ($ExpectedEdgeUsV2Vmess -ge 0) {
    $cmd += @('--expected-edge-us-v2-vmess', $ExpectedEdgeUsV2Vmess)
  }
  if ($ExpectedEdgeUsV2Hy2 -ge 0) {
    $cmd += @('--expected-edge-us-v2-hy2', $ExpectedEdgeUsV2Hy2)
  }
  if ($SkipHttp) { $cmd += '--skip-http' }

  Write-Info 'running remote read-only verification'
  & ssh @sshArgs ($cmd -join ' ')
  if ($LASTEXITCODE -ne 0) { throw 'remote verification failed' }
  Write-Ok 'remote verification passed'
} finally {
  $cleanupCmd = @(
    'rm -f ' + (Quote-Remote "$remoteStage/remote-verify-substore.py") + ' ' + (Quote-Remote "$remoteStage/ai-routing-contract.json") + ' ' + (Quote-Remote "$remoteStage/ai-routing-rules.json"),
    'rmdir ' + (Quote-Remote $remoteStage) + ' 2>/dev/null || true'
  ) -join ' && '
  & ssh @sshArgs $cleanupCmd | Out-Null
}
