<#
.SYNOPSIS
  Verify subscription-hub outputs without printing subscription URLs.
#>

[CmdletBinding()]
param(
  [string]$EnvFile = $env:HUB_RUNTIME_ENV,
  [string]$BaseUrl = $env:HUB_PUBLIC_BASE_URL,
  [string]$HubPagePath = $env:HUB_PAGE_PATH,
  [string]$SparklePath = $env:HUB_SPARKLE_PATH,
  [string]$ShadowrocketConfigPath = $env:HUB_SHADOWROCKET_CONFIG_PATH,
  [string]$IosOrdinaryPath = $env:HUB_IOS_ORDINARY_PATH,
  [string]$IosHy2Path = $env:HUB_IOS_HY2_PATH,
  [string]$BasicAuthUser = $env:HUB_BASIC_AUTH_USER,
  [string]$BasicAuthPassword = $env:HUB_BASIC_AUTH_PASSWORD,
  [string]$OutFile = $env:HUB_VERIFY_STATUS_OUT,
  [string]$UploadSshHost = $env:HUB_VERIFY_UPLOAD_SSH_HOST,
  [string]$UploadSshPort = $env:HUB_VERIFY_UPLOAD_SSH_PORT,
  [string]$UploadSshUser = $env:HUB_VERIFY_UPLOAD_SSH_USER,
  [string]$UploadSshKey = $env:HUB_VERIFY_UPLOAD_SSH_KEY,
  [string]$RemoteStatusPath = $env:HUB_VERIFY_REMOTE_STATUS_PATH,
  [string]$RemoteCheckHost = $env:HUB_VERIFY_REMOTE_CHECK_HOST,
  [string]$RuleRegistryPath = $env:HUB_RULE_REGISTRY_PATH,
  [int]$HttpAttempts = 3,
  [int]$HttpRetryDelaySeconds = 2,
  [switch]$SkipHttp
)

$ErrorActionPreference = 'Stop'

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

function Read-EnvValue($Path, $Key) {
  if (-not (Test-Path -LiteralPath $Path)) { return '' }
  foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
    if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") {
      return $Matches[1].Trim().Trim('"').Trim("'")
    }
  }
  return ''
}

if (-not $EnvFile) {
  $EnvFile = Join-Path (Split-Path -Parent $PSCommandPath) 'examples\hub.runtime.env.example'
}

if (Test-Path -LiteralPath $EnvFile) {
  if (-not $BaseUrl) { $BaseUrl = Read-EnvValue $EnvFile 'HUB_PUBLIC_BASE_URL' }
  if (-not $HubPagePath) { $HubPagePath = Read-EnvValue $EnvFile 'HUB_PAGE_PATH' }
  if (-not $SparklePath) { $SparklePath = Read-EnvValue $EnvFile 'HUB_SPARKLE_PATH' }
  if (-not $ShadowrocketConfigPath) { $ShadowrocketConfigPath = Read-EnvValue $EnvFile 'HUB_SHADOWROCKET_CONFIG_PATH' }
  if (-not $IosOrdinaryPath) { $IosOrdinaryPath = Read-EnvValue $EnvFile 'HUB_IOS_ORDINARY_PATH' }
  if (-not $IosHy2Path) { $IosHy2Path = Read-EnvValue $EnvFile 'HUB_IOS_HY2_PATH' }
  if (-not $BasicAuthUser) { $BasicAuthUser = Read-EnvValue $EnvFile 'HUB_BASIC_AUTH_USER' }
  if (-not $BasicAuthPassword) { $BasicAuthPassword = Read-EnvValue $EnvFile 'HUB_BASIC_AUTH_PASSWORD' }
}

if (-not $RuleRegistryPath) {
  $RuleRegistryPath = Join-Path (Split-Path -Parent $PSCommandPath) 'rules.json'
}

function Assert-Path($Name, $Value) {
  if (-not $Value -or -not $Value.StartsWith('/')) { throw "$Name must start with /" }
  if ($Value -match '[\s''"`;]') { throw "$Name contains unsafe characters" }
}

function Join-Url($Base, $Path) {
  return $Base.TrimEnd('/') + $Path
}

function Get-SafeHash($Text) {
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()).Substring(0,16)
  } finally {
    $sha.Dispose()
  }
}

function Get-NowIso {
  return [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:sszzz')
}

function Get-SshArgs($HostName) {
  $args = @()
  if ($UploadSshKey) { $args += @('-i', $UploadSshKey) }
  if ($UploadSshPort) { $args += @('-p', $UploadSshPort) }
  $args += @('-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new', "$UploadSshUser@$HostName")
  return $args
}

function Get-ScpArgs {
  $args = @()
  if ($UploadSshKey) { $args += @('-i', $UploadSshKey) }
  if ($UploadSshPort) { $args += @('-P', $UploadSshPort) }
  $args += @('-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new')
  return $args
}

function Get-BodyShape($Label, $Text) {
  if ($Label -eq 'sparkle') {
    $requiredSections = @('proxies', 'proxy-groups', 'rules', 'rule-providers', 'dns')
    $missingSections = @()
    foreach ($section in $requiredSections) {
      if ($Text -notmatch "(?m)^$([regex]::Escape($section)):\s*") {
        $missingSections += $section
      }
    }
    if ($missingSections.Count -gt 0) {
      throw "sparkle endpoint is not a full mihomo profile; missing sections: $($missingSections -join ', ')"
    }
    return 'mihomo-profile'
  }
  if ($Label -eq 'shadowrocket-config') {
    foreach ($section in @('[General]', '[Proxy Group]', '[Rule]')) {
      if (-not $Text.Contains($section)) { throw "shadowrocket config missing $section" }
    }
    return 'shadowrocket-conf'
  }
  if ($Label -eq 'ios-ordinary') {
    if ($Text -match '(?i)hysteria2|hy2|type:\s*hysteria2') {
      throw 'ordinary iOS feed contains HY2-looking content'
    }
    if ($Text -notmatch '(?i)(vmess://|vless://|ss://|trojan://)') {
      throw 'ordinary iOS feed does not look like URI content'
    }
    return 'uri-feed'
  }
  if ($Label -eq 'ios-hy2') {
    if ($Text -notmatch '(?i)(hysteria2|hy2|type:\s*hysteria2)') {
      throw 'HY2 iOS feed does not contain HY2-looking content'
    }
    return 'hy2-feed'
  }
  return 'unknown'
}

function Get-RuleRegistry($Path) {
  if (-not (Test-Path -LiteralPath $Path)) { throw "missing rule registry: $Path" }
  $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
  $registry = $raw | ConvertFrom-Json
  if (-not $registry.items -or $registry.items.Count -le 0) { throw 'rule registry has no items' }
  if ($registry.version -ne 2) { throw "rule registry version must be 2, got $($registry.version)" }
  if (-not $registry.rules_path_prefix -or -not $registry.rules_path_prefix.StartsWith('/')) { throw 'rule registry rules_path_prefix must start with /' }
  $activeItems = @($registry.items | Where-Object { $_.enabled -ne $false })
  $srCount = @($activeItems | Where-Object { $_.client -eq 'shadowrocket' }).Count
  $mihomoCount = @($activeItems | Where-Object { $_.client -eq 'mihomo' }).Count
  $srLogicalCount = @($activeItems | Where-Object { $_.client -eq 'shadowrocket' -and $_.logical_provider }).Count
  $mihomoLogicalCount = @($activeItems | Where-Object { $_.client -eq 'mihomo' -and $_.logical_provider }).Count
  if ($srCount -le 0 -or $mihomoCount -le 0) { throw 'active registry must include both Shadowrocket and Mihomo mirrors' }
  $seen = @{}
  foreach ($item in $activeItems) {
    if ($item.id -notmatch '^[a-z0-9][a-z0-9-]*$') { throw "invalid rule id: $($item.id)" }
    if ($item.file -notmatch '^[A-Za-z0-9._-]+$') { throw "invalid rule file: $($item.file)" }
    if ($seen.ContainsKey($item.file)) { throw "duplicate rule file: $($item.file)" }
    $seen[$item.file] = $true
  }
  $requiredLogicalProviders = @('ai-openai', 'ai-anthropic', 'ai-xai', 'ai-community-supplement')
  foreach ($client in @('shadowrocket', 'mihomo')) {
    $logicalItems = @($activeItems | Where-Object { $_.client -eq $client -and $_.logical_provider })
    foreach ($logicalProvider in $requiredLogicalProviders) {
      if (@($logicalItems | Where-Object { $_.logical_provider -eq $logicalProvider }).Count -ne 1) {
        throw "active $client registry must contain exactly one $logicalProvider logical provider"
      }
    }
    if (@($logicalItems | Where-Object { $_.format -eq 'mrs' }).Count -gt 0) {
      throw "active $client logical providers must not publish opaque MRS"
    }
  }
  return [pscustomobject]@{
    items = $activeItems
    rules_path_prefix = $registry.rules_path_prefix
    counts = [pscustomobject]@{
      total = $activeItems.Count
      disabled = @($registry.items | Where-Object { $_.enabled -eq $false }).Count
      shadowrocket = $srCount
      mihomo = $mihomoCount
      shadowrocket_logical = $srLogicalCount
      mihomo_logical = $mihomoLogicalCount
    }
  }
}

function Get-RuleMirrorShape($Item, $Text, [int]$Bytes) {
  if ($Bytes -le 8) { throw "rule mirror too small: $($Item.id)" }
  if ($Text -match '(?is)<html|<!doctype html') { throw "rule mirror looks like HTML: $($Item.id)" }
  if ($Item.format -eq 'mrs') { return 'mrs' }
  $lines = @($Text -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') -and -not $_.StartsWith('//') })
  if ($lines.Count -le 0) { throw "rule mirror has no usable text lines: $($Item.id)" }
  if ($Item.rule_type -eq 'DOMAIN-SET') {
    if (-not ($lines | Where-Object { $_ -notmatch ',' -and $_ -match '\.' } | Select-Object -First 1)) {
      throw "DOMAIN-SET mirror shape mismatch: $($Item.id)"
    }
    return 'domain-set'
  }
  $payloadDomain = ($lines | Where-Object { $_ -eq 'payload:' } | Select-Object -First 1) -and ($lines | Where-Object { $_ -match "^\s*-\s*['""]?\+?\.[^'""]+\.[^'""]+['""]?\s*$" } | Select-Object -First 1)
  if (-not ($lines | Where-Object { $_ -match ',' } | Select-Object -First 1) -and -not $payloadDomain) {
    throw "text rule mirror shape mismatch: $($Item.id)"
  }
  if ($Item.logical_provider -and ($lines | Where-Object { $_ -match '^DOMAIN-SUFFIX\s*,\s*x[.]com\s*$' } | Select-Object -First 1)) {
    throw "logical AI mirror contains forbidden broad x.com rule: $($Item.id)"
  }
  if ($payloadDomain) { return 'payload-domain' }
  return 'text-rule'
}

function Get-CurlBaseArgs {
  $args = @('-sS', '--noproxy', '*', '--connect-timeout', '20', '--max-time', '120')
  if ($RemoteCheckHost) {
    $hostName = ([Uri]$BaseUrl).Host
    $args += @('--resolve', "${hostName}:443:127.0.0.1", '-k')
  }
  return $args
}

function Invoke-HubCurl {
  param(
    [string]$Path,
    [string]$OutputPath,
    [string]$Auth
  )
  $args = Get-CurlBaseArgs
  if ($Auth) { $args += @('-u', $Auth) }
  $args += @('-o', $OutputPath, '-w', '%{http_code}', (Join-Url $BaseUrl $Path))
  for ($attempt = 1; $attempt -le [Math]::Max(1, $HttpAttempts); $attempt++) {
    if ($RemoteCheckHost) {
      $payload = [ordered]@{ args = $args }
      $json = $payload | ConvertTo-Json -Compress -Depth 4
      $encodedJson = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json))
      $script = @"
import base64, json, subprocess, sys
payload = json.loads(base64.b64decode('$encodedJson').decode('utf-8'))
proc = subprocess.run(['curl'] + payload['args'], text=True, capture_output=True)
if proc.stdout:
    sys.stdout.write(proc.stdout)
if proc.stderr:
    sys.stderr.write(proc.stderr)
raise SystemExit(proc.returncode)
"@
      $sshArgs = Get-SshArgs $RemoteCheckHost
      $result = $script | & ssh @sshArgs 'python3 -'
    } else {
      $result = & curl.exe @args
    }
    $httpCode = ($result -join '').Trim()
    $retryableHttp = $httpCode -match '^5[0-9][0-9]$'
    if (($LASTEXITCODE -eq 0 -and -not $retryableHttp) -or $attempt -eq [Math]::Max(1, $HttpAttempts)) {
      return $result
    }
    if ($HttpRetryDelaySeconds -gt 0) {
      Start-Sleep -Seconds $HttpRetryDelaySeconds
    }
  }
}

foreach ($pair in @(
  @('HubPagePath', $HubPagePath),
  @('SparklePath', $SparklePath),
  @('ShadowrocketConfigPath', $ShadowrocketConfigPath),
  @('IosOrdinaryPath', $IosOrdinaryPath),
  @('IosHy2Path', $IosHy2Path)
)) {
  Assert-Path $pair[0] $pair[1]
}

Write-Ok 'subscription hub parameter shape passed'

$ruleRegistry = Get-RuleRegistry $RuleRegistryPath
Write-Ok ("rule registry shape passed: total={0} disabled={1} shadowrocket={2} mihomo={3}" -f $ruleRegistry.counts.total, $ruleRegistry.counts.disabled, $ruleRegistry.counts.shadowrocket, $ruleRegistry.counts.mihomo)

if ($SkipHttp) {
  Write-Warn2 'HTTP checks skipped'
  exit 0
}

if (-not $BaseUrl) { throw 'BaseUrl or HUB_PUBLIC_BASE_URL is required for HTTP checks' }

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl -and -not $RemoteCheckHost) { throw 'curl.exe is required for local HTTP checks' }
if ($RemoteCheckHost -and -not $UploadSshUser) { $UploadSshUser = 'root' }

Write-Info 'checking protected hub page unauthorized status'
$nullOut = if ($RemoteCheckHost) { '/tmp/subscription-hub-verify-null' } else { 'NUL' }
$rootCode = Invoke-HubCurl -Path '/' -OutputPath $nullOut
if ($LASTEXITCODE -ne 0) { throw 'curl hub root redirect check failed' }
if ($rootCode -ne '302') { throw "expected hub root HTTP 302 redirect, got $rootCode" }
Write-Ok 'hub root redirects to page path'

$pageRedirectCode = Invoke-HubCurl -Path $HubPagePath -OutputPath $nullOut
if ($LASTEXITCODE -ne 0) { throw 'curl protected page redirect check failed' }
if ($pageRedirectCode -ne '302') { throw "expected hub page path HTTP 302 redirect, got $pageRedirectCode" }
Write-Ok 'hub page path redirects to trailing slash'

$pageCheckPath = $HubPagePath.TrimEnd('/') + '/'
$pageUnauthCode = Invoke-HubCurl -Path $pageCheckPath -OutputPath $nullOut
if ($LASTEXITCODE -ne 0) { throw 'curl protected page check failed' }
if ($pageUnauthCode -ne '401') { throw "expected protected page HTTP 401, got $pageUnauthCode" }
Write-Ok 'hub page returns 401 without login'

if (-not ($BasicAuthUser -and $BasicAuthPassword)) {
  throw 'Basic Auth env is required for hub page verification'
}

$auth = "$BasicAuthUser`:$BasicAuthPassword"
$pageTmp = New-TemporaryFile
try {
  $pageOut = if ($RemoteCheckHost) { '/tmp/subscription-hub-verify-page' } else { $pageTmp.FullName }
  $pageCode = Invoke-HubCurl -Path $pageCheckPath -OutputPath $pageOut -Auth $auth
  if ($LASTEXITCODE -ne 0) { throw 'curl authorized page check failed' }
  if ($pageCode -ne '200') { throw "expected authorized hub page HTTP 200, got $pageCode" }
  if ($RemoteCheckHost) {
    $sshArgs = Get-SshArgs $RemoteCheckHost
    $pageText = & ssh @sshArgs "cat /tmp/subscription-hub-verify-page; rm -f /tmp/subscription-hub-verify-page"
    $pageText = $pageText -join "`n"
  } else {
    $pageText = Get-Content -LiteralPath $pageTmp.FullName -Raw -Encoding UTF8
  }
  if ($pageText -notmatch 'links[.]json' -or $pageText -notmatch 'shadowrocket-section') {
    throw 'authorized page does not look like the subscription hub page'
  }
  Write-Ok 'hub page authorized returns 200'
} finally {
  Remove-Item -LiteralPath $pageTmp.FullName -ErrorAction SilentlyContinue
}

$linksPath = $HubPagePath.TrimEnd('/') + '/links.json'
$linksUnauthCode = Invoke-HubCurl -Path $linksPath -OutputPath $nullOut
if ($LASTEXITCODE -ne 0) { throw 'curl protected links check failed' }
if ($linksUnauthCode -ne '401') { throw "expected protected links HTTP 401, got $linksUnauthCode" }
Write-Ok 'protected links json returns 401 without login'

$linksAuthCode = Invoke-HubCurl -Path $linksPath -OutputPath $nullOut -Auth $auth
if ($LASTEXITCODE -ne 0) { throw 'curl authorized links check failed' }
if ($linksAuthCode -ne '200') { throw "expected authorized links HTTP 200, got $linksAuthCode" }
Write-Ok 'protected links json authorized responds'

$statusPath = $HubPagePath.TrimEnd('/') + '/status.json'
$statusUnauthCode = Invoke-HubCurl -Path $statusPath -OutputPath $nullOut
if ($LASTEXITCODE -ne 0) { throw 'curl protected status check failed' }
if ($statusUnauthCode -ne '401') { throw "expected protected status HTTP 401, got $statusUnauthCode" }
Write-Ok 'protected status json returns 401 without login'

$statusAuthCode = Invoke-HubCurl -Path $statusPath -OutputPath $nullOut -Auth $auth
if ($LASTEXITCODE -ne 0) { throw 'curl authorized status check failed' }
if ($statusAuthCode -ne '200') { throw "expected authorized status HTTP 200, got $statusAuthCode" }
Write-Ok 'protected status json authorized responds'

$ruleStatusTmp = New-TemporaryFile
try {
  $ruleStatusPath = $ruleRegistry.rules_path_prefix.TrimEnd('/') + '/status.json'
  $ruleStatusOut = if ($RemoteCheckHost) { '/tmp/subscription-hub-rule-status' } else { $ruleStatusTmp.FullName }
  $ruleStatusCode = Invoke-HubCurl -Path $ruleStatusPath -OutputPath $ruleStatusOut
  if ($LASTEXITCODE -ne 0) { throw 'curl rule semantic status check failed' }
  if ($ruleStatusCode -ne '200') { throw "expected rule semantic status HTTP 200, got $ruleStatusCode" }
  if ($RemoteCheckHost) {
    $sshArgs = Get-SshArgs $RemoteCheckHost
    $ruleStatusText = & ssh @sshArgs "cat '$ruleStatusOut'; rm -f '$ruleStatusOut'"
    $ruleStatusText = $ruleStatusText -join "`n"
  } else {
    $ruleStatusText = Get-Content -LiteralPath $ruleStatusTmp.FullName -Raw -Encoding UTF8
  }
  $ruleSemanticStatus = $ruleStatusText | ConvertFrom-Json
  if ($ruleSemanticStatus.summary.total -ne $ruleRegistry.counts.total) {
    throw "rule semantic status total mismatch: expected $($ruleRegistry.counts.total), got $($ruleSemanticStatus.summary.total)"
  }
  if ($ruleSemanticStatus.summary.ok -ne $ruleRegistry.counts.total -or $ruleSemanticStatus.summary.failed -ne 0 -or $ruleSemanticStatus.summary.stale -ne 0) {
    throw "rule semantic status is not complete and fresh: ok=$($ruleSemanticStatus.summary.ok) stale=$($ruleSemanticStatus.summary.stale) failed=$($ruleSemanticStatus.summary.failed)"
  }
  if (-not $ruleSemanticStatus.registry -or -not $ruleSemanticStatus.registry.clients -or -not $ruleSemanticStatus.registry.logical_providers) {
    throw 'rule semantic status is missing registry-derived counts'
  }
  if ($ruleSemanticStatus.registry.total -ne $ruleRegistry.counts.total -or
      $ruleSemanticStatus.registry.disabled -ne $ruleRegistry.counts.disabled -or
      $ruleSemanticStatus.registry.clients.shadowrocket -ne $ruleRegistry.counts.shadowrocket -or
      $ruleSemanticStatus.registry.clients.mihomo -ne $ruleRegistry.counts.mihomo -or
      $ruleSemanticStatus.registry.logical_providers.shadowrocket -ne $ruleRegistry.counts.shadowrocket_logical -or
      $ruleSemanticStatus.registry.logical_providers.mihomo -ne $ruleRegistry.counts.mihomo_logical) {
    throw 'rule semantic status registry-derived counts do not match the active registry'
  }
  $semanticById = @{}
  foreach ($statusItem in @($ruleSemanticStatus.items)) { $semanticById[$statusItem.id] = $statusItem }
  if (@($ruleSemanticStatus.items).Count -ne $ruleRegistry.counts.total -or $semanticById.Count -ne $ruleRegistry.counts.total) {
    throw 'rule semantic status item count or id uniqueness does not match the active registry'
  }
  foreach ($registryItem in @($ruleRegistry.items)) {
    if (-not $semanticById.ContainsKey($registryItem.id)) { throw "rule semantic status missing registry item: $($registryItem.id)" }
    $statusItem = $semanticById[$registryItem.id]
    if ($statusItem.status -ne 'ok') {
      throw "registry item status is not ok: $($registryItem.id)"
    }
    if ($registryItem.logical_provider -and $statusItem.semantic.status -ne 'passed') {
      throw "logical provider semantic gate not passed: $($registryItem.id)"
    }
    if ($registryItem.logical_provider -and $statusItem.logical_provider -ne $registryItem.logical_provider) {
      throw "logical provider status identity mismatch: $($registryItem.id)"
    }
  }
  Write-Ok ("rule semantic status passed: total={0} logical={1}" -f $ruleSemanticStatus.summary.total, @($ruleRegistry.items | Where-Object { $_.logical_provider }).Count)
} finally {
  Remove-Item -LiteralPath $ruleStatusTmp.FullName -ErrorAction SilentlyContinue
}

$checks = @(
  @('sparkle', $SparklePath),
  @('shadowrocket-config', $ShadowrocketConfigPath),
  @('ios-ordinary', $IosOrdinaryPath),
  @('ios-hy2', $IosHy2Path)
)

$statusItems = [ordered]@{}
$statusItems['sparkle'] = [ordered]@{ id = 'sparkle'; title = 'Sparkle / FlClash / OpenClash'; status = 'pending'; url_hash = Get-SafeHash (Join-Url $BaseUrl $SparklePath); last_checked = ''; bytes = $null; sha256 = ''; shape = '' }
$statusItems['sr-config'] = [ordered]@{ id = 'sr-config'; title = 'Shadowrocket config'; status = 'pending'; url_hash = Get-SafeHash (Join-Url $BaseUrl $ShadowrocketConfigPath); last_checked = ''; bytes = $null; sha256 = ''; shape = '' }
$statusItems['ios-ordinary'] = [ordered]@{ id = 'ios-ordinary'; title = 'Shadowrocket ordinary nodes'; status = 'pending'; url_hash = Get-SafeHash (Join-Url $BaseUrl $IosOrdinaryPath); last_checked = ''; bytes = $null; sha256 = ''; shape = '' }
$statusItems['ios-hy2'] = [ordered]@{ id = 'ios-hy2'; title = 'Shadowrocket HY2 nodes'; status = 'pending'; url_hash = Get-SafeHash (Join-Url $BaseUrl $IosHy2Path); last_checked = ''; bytes = $null; sha256 = ''; shape = '' }

foreach ($check in $checks) {
  $label = $check[0]
  $path = $check[1]
  $tmp = New-TemporaryFile
  try {
    $endpointOut = if ($RemoteCheckHost) { "/tmp/subscription-hub-verify-$label" } else { $tmp.FullName }
    $code = Invoke-HubCurl -Path $path -OutputPath $endpointOut
    if ($LASTEXITCODE -ne 0) { throw "curl failed for $label" }
    if ($code -ne '200') { throw "$label endpoint HTTP $code" }
    if ($RemoteCheckHost) {
      $sshArgs = Get-SshArgs $RemoteCheckHost
      $bytes = [int](& ssh @sshArgs "wc -c < '$endpointOut'")
      $text = & ssh @sshArgs "cat '$endpointOut'; rm -f '$endpointOut'"
      $text = $text -join "`n"
    } else {
      $bytes = (Get-Item -LiteralPath $tmp.FullName).Length
      $text = Get-Content -LiteralPath $tmp.FullName -Raw -Encoding UTF8
    }
    if ($bytes -le 0) { throw "$label endpoint returned empty body" }
    $shape = Get-BodyShape $label $text
    if ($RemoteCheckHost) {
      $hashBytes = [System.Text.Encoding]::UTF8.GetBytes($text)
      $sha = [System.Security.Cryptography.SHA256]::Create()
      try {
        $hash = ([BitConverter]::ToString($sha.ComputeHash($hashBytes)).Replace('-', '').ToLowerInvariant()).Substring(0,16)
      } finally {
        $sha.Dispose()
      }
    } else {
      $hash = (Get-FileHash -LiteralPath $tmp.FullName -Algorithm SHA256).Hash.ToLowerInvariant().Substring(0,16)
    }
    $itemId = if ($label -eq 'shadowrocket-config') { 'sr-config' } else { $label }
    $statusItems[$itemId].status = 'ok'
    $statusItems[$itemId].last_checked = Get-NowIso
    $statusItems[$itemId].bytes = $bytes
    $statusItems[$itemId].sha256 = $hash
    $statusItems[$itemId].shape = $shape
    Write-Ok ("{0}: http=200 bytes={1} sha256={2} shape={3}" -f $label, $bytes, $hash, $shape)
  } finally {
    Remove-Item -LiteralPath $tmp.FullName -ErrorAction SilentlyContinue
  }
}

$ruleOk = 0
$ruleBytesTotal = 0
$ruleHashes = New-Object System.Collections.Generic.List[string]
foreach ($item in $ruleRegistry.items) {
  $tmp = New-TemporaryFile
  try {
    $rulePath = $ruleRegistry.rules_path_prefix.TrimEnd('/') + '/' + $item.file
    $ruleOut = if ($RemoteCheckHost) { "/tmp/subscription-hub-rule-$($item.id)" } else { $tmp.FullName }
    $code = Invoke-HubCurl -Path $rulePath -OutputPath $ruleOut
    if ($LASTEXITCODE -ne 0) { throw "curl failed for rule mirror $($item.id)" }
    if ($code -ne '200') { throw "rule mirror $($item.id) endpoint HTTP $code" }
    if ($RemoteCheckHost) {
      $sshArgs = Get-SshArgs $RemoteCheckHost
      $bytes = [int](& ssh @sshArgs "wc -c < '$ruleOut'")
      $text = & ssh @sshArgs "cat '$ruleOut'; rm -f '$ruleOut'"
      $text = $text -join "`n"
      $hashBytes = [System.Text.Encoding]::UTF8.GetBytes($text)
      $sha = [System.Security.Cryptography.SHA256]::Create()
      try {
        $hash = ([BitConverter]::ToString($sha.ComputeHash($hashBytes)).Replace('-', '').ToLowerInvariant()).Substring(0,16)
      } finally {
        $sha.Dispose()
      }
    } else {
      $bytes = (Get-Item -LiteralPath $tmp.FullName).Length
      $text = Get-Content -LiteralPath $tmp.FullName -Raw -Encoding UTF8
      $hash = (Get-FileHash -LiteralPath $tmp.FullName -Algorithm SHA256).Hash.ToLowerInvariant().Substring(0,16)
    }
    $shape = Get-RuleMirrorShape $item $text $bytes
    $ruleOk += 1
    $ruleBytesTotal += $bytes
    $ruleHashes.Add($hash) | Out-Null
    Write-Ok ("rule {0}: http=200 bytes={1} sha256={2} shape={3}" -f $item.id, $bytes, $hash, $shape)
  } finally {
    Remove-Item -LiteralPath $tmp.FullName -ErrorAction SilentlyContinue
  }
}

$statusItems['rule-mirror'] = [ordered]@{
  id = 'rule-mirror'
  title = 'Third-party rule mirrors'
  status = 'ok'
  url_hash = ''
  last_checked = Get-NowIso
  bytes = $ruleBytesTotal
  sha256 = Get-SafeHash ($ruleHashes -join '|')
  shape = "rule-mirror:$ruleOk/$($ruleRegistry.items.Count)"
  summary = [ordered]@{ total = $ruleRegistry.items.Count; ok = $ruleOk; stale = 0; failed = 0 }
}

$verifiedAt = Get-NowIso
if (-not $OutFile) {
  $OutFile = Join-Path (Split-Path -Parent $PSCommandPath) '.secrets.local\verify-status.json'
}
$statusPayload = [ordered]@{
  generated_at = $verifiedAt
  verified_at = $verifiedAt
  items = @($statusItems.Values)
}
$outDir = Split-Path -Parent $OutFile
if ($outDir) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$statusJson = ($statusPayload | ConvertTo-Json -Depth 6) + [Environment]::NewLine
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutFile, $statusJson, $utf8NoBom)
Write-Ok ("status json written bytes={0} sha256={1}" -f (Get-Item -LiteralPath $OutFile).Length, (Get-FileHash -LiteralPath $OutFile -Algorithm SHA256).Hash.ToLowerInvariant().Substring(0,16))

if ($UploadSshHost) {
  if (-not $UploadSshUser) { $UploadSshUser = 'root' }
  if (-not $RemoteStatusPath) { $RemoteStatusPath = '/opt/frontier/subscription-hub/public/status.json' }
  $scpArgs = Get-ScpArgs
  & scp @scpArgs $OutFile "${UploadSshUser}@${UploadSshHost}:$RemoteStatusPath"
  if ($LASTEXITCODE -ne 0) { throw 'status upload failed' }
  Write-Ok 'status json uploaded'
}
