<#
.SYNOPSIS
  Measure application-visible latency for US Edge VMess and HY2 nodes.

.DESCRIPTION
  This script reads an existing Mihomo YAML profile, extracts matching nodes,
  starts a separate temporary Mihomo core on private local ports, switches a
  test selector node by node through the controller API, and measures real HTTP
  request timings through curl using socks5h.

  It does not modify Sparkle, Sub-Store, or the source profile. The generated
  temporary profile contains proxy credentials, so it is written under
  .secrets.local by default.

.EXAMPLE
  pwsh -File .\scripts\measure-edge-real-latency.ps1

.EXAMPLE
  pwsh -File .\scripts\measure-edge-real-latency.ps1 -Samples 10 -Warmup 2

.EXAMPLE
  pwsh -File .\scripts\measure-edge-real-latency.ps1 -ManualMenu

.EXAMPLE
  pwsh -File .\scripts\measure-edge-real-latency.ps1 -ManualMenu -SkipAutoMeasure -KeepCoreAlive
#>

[CmdletBinding()]
param(
  [string]$ProfilePath = 'D:\scoop\apps\sparkle\current\data\work\config.yaml',
  [string]$MihomoPath = 'D:\scoop\apps\sparkle\current\resources\sidecar\mihomo.exe',
  [string]$Python = 'python',

  [string[]]$Nodes = @(),
  [string]$NodeNameRegex = '^US-Edge \|',

  [string[]]$Urls = @(
    'https://www.gstatic.com/generate_204',
    'https://cp.cloudflare.com/generate_204',
    'https://chatgpt.com/',
    'https://claude.ai/',
    'https://api.openai.com/v1/models'
  ),

  [int]$Samples = 7,
  [int]$Warmup = 1,
  [int]$TimeoutSeconds = 20,
  [int]$ConnectTimeoutSeconds = 8,
  [int]$MixedPort = 17997,
  [int]$ControllerPort = 19097,
  [string]$ControllerDelayUrl = 'https://www.gstatic.com/generate_204',
  [int]$PauseBetweenNodesMs = 800,
  [int]$ManualHoldSeconds = 0,
  [switch]$ManualMenu,
  [switch]$SkipAutoMeasure,
  [switch]$KeepCoreAlive,
  [string]$WorkDir = '.secrets.local\latency-lab'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not [System.IO.Path]::IsPathRooted($WorkDir)) {
  $WorkDir = Join-Path $RepoRoot $WorkDir
}

$Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$RunDir = Join-Path $WorkDir $Timestamp
$ConfigPath = Join-Path $RunDir 'mihomo-real-latency.yaml'
$RawCsvPath = Join-Path $RunDir 'raw.csv'
$SummaryCsvPath = Join-Path $RunDir 'summary-by-url.csv'
$OverallCsvPath = Join-Path $RunDir 'summary-overall.csv'
$MihomoStdoutPath = Join-Path $RunDir 'mihomo.stdout.log'
$MihomoStderrPath = Join-Path $RunDir 'mihomo.stderr.log'
$GroupName = 'REAL-LATENCY-TEST'

function Write-Info($Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn2($Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }

function Assert-File($Path, $Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label not found: $Path"
  }
}

function Assert-PortFree($Port, $Label) {
  $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener) {
    throw "$Label port $Port is already listening. Choose another port with -MixedPort or -ControllerPort."
  }
}

function ConvertTo-JsonArray([string[]]$Items) {
  if ($null -eq $Items -or $Items.Count -eq 0) {
    return '[]'
  }
  return ConvertTo-Json -InputObject @($Items) -Compress
}

function Convert-SecondsToMs($Text) {
  if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
  $culture = [System.Globalization.CultureInfo]::InvariantCulture
  try {
    return [Math]::Round(([double]::Parse($Text, $culture) * 1000.0), 1)
  } catch {
    return $null
  }
}

function Get-Percentile([double[]]$Values, [double]$Percent) {
  if ($null -eq $Values -or $Values.Count -eq 0) { return $null }
  $sorted = @($Values | Sort-Object)
  $index = [int][Math]::Ceiling(($Percent / 100.0) * $sorted.Count) - 1
  if ($index -lt 0) { $index = 0 }
  if ($index -ge $sorted.Count) { $index = $sorted.Count - 1 }
  return [Math]::Round([double]$sorted[$index], 1)
}

function Round-OrNull($Value) {
  if ($null -eq $Value) { return $null }
  return [Math]::Round([double]$Value, 1)
}

function Invoke-ControllerJson($Method, $Path, $BodyObject) {
  $uri = "http://127.0.0.1:$ControllerPort$Path"
  $args = @{
    Method = $Method
    Uri = $uri
    TimeoutSec = 8
  }
  if ($null -ne $BodyObject) {
    $args['ContentType'] = 'application/json'
    $args['Body'] = ($BodyObject | ConvertTo-Json -Compress)
  }
  return Invoke-RestMethod @args
}

function Set-TestNode($NodeName) {
  $encodedGroup = [Uri]::EscapeDataString($GroupName)
  Invoke-ControllerJson -Method Put -Path "/proxies/$encodedGroup" -BodyObject @{ name = $NodeName } | Out-Null
}

function Get-ControllerDelay($NodeName) {
  $encodedNode = [Uri]::EscapeDataString($NodeName)
  $encodedUrl = [Uri]::EscapeDataString($ControllerDelayUrl)
  try {
    $delay = Invoke-ControllerJson -Method Get -Path "/proxies/$encodedNode/delay?timeout=8000&url=$encodedUrl" -BodyObject $null
    return $delay.delay
  } catch {
    return $null
  }
}

function Invoke-CurlTiming($NodeName, $Url, $Iteration, [bool]$IsWarmup) {
  $writeOut = "code=%{http_code}`tnamelookup=%{time_namelookup}`tconnect=%{time_connect}`tappconnect=%{time_appconnect}`tstarttransfer=%{time_starttransfer}`ttotal=%{time_total}`n"
  $curlArgs = @(
    '--silent',
    '--show-error',
    '--location',
    '--compressed',
    '--output', 'NUL',
    '--max-time', "$TimeoutSeconds",
    '--connect-timeout', "$ConnectTimeoutSeconds",
    '--proxy', "socks5h://127.0.0.1:$MixedPort",
    '--write-out', $writeOut,
    $Url
  )

  $output = & curl.exe @curlArgs 2>&1
  $exitCode = $LASTEXITCODE
  $metricLine = @($output | Where-Object { $_ -is [string] -and $_ -like 'code=*' } | Select-Object -Last 1)
  $errorText = @($output | Where-Object { -not ($_ -is [string] -and $_ -like 'code=*') }) -join ' '

  $values = @{}
  if ($metricLine) {
    foreach ($part in ($metricLine -split "`t")) {
      $kv = $part -split '=', 2
      if ($kv.Count -eq 2) { $values[$kv[0]] = $kv[1] }
    }
  }

  [pscustomobject]@{
    Timestamp = (Get-Date).ToString('s')
    Node = $NodeName
    Url = $Url
    Iteration = $Iteration
    Warmup = $IsWarmup
    ExitCode = $exitCode
    HttpCode = $values['code']
    NameLookupMs = Convert-SecondsToMs $values['namelookup']
    ConnectMs = Convert-SecondsToMs $values['connect']
    AppConnectMs = Convert-SecondsToMs $values['appconnect']
    StartTransferMs = Convert-SecondsToMs $values['starttransfer']
    TotalMs = Convert-SecondsToMs $values['total']
    Error = $errorText
  }
}

function Wait-ControllerReady() {
  for ($i = 0; $i -lt 60; $i++) {
    try {
      Invoke-ControllerJson -Method Get -Path '/version' -BodyObject $null | Out-Null
      return
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  throw "Mihomo controller did not become ready. Check $MihomoStderrPath"
}

function Show-ManualMenu([string[]]$SelectedNodes) {
  Write-Host ''
  Write-Info "Manual menu is using SOCKS5/HTTP mixed proxy 127.0.0.1:$MixedPort"
  Write-Info "Set a browser or test tool to this local proxy, then switch nodes below."
  while ($true) {
    Write-Host ''
    for ($i = 0; $i -lt $SelectedNodes.Count; $i++) {
      Write-Host ("{0}. {1}" -f ($i + 1), $SelectedNodes[$i])
    }
    $choice = Read-Host 'Choose node number, or q to quit manual menu'
    if ($choice -match '^(q|quit|exit)$') { break }
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number)) {
      Write-Warn2 'Invalid choice.'
      continue
    }
    if ($number -lt 1 -or $number -gt $SelectedNodes.Count) {
      Write-Warn2 'Choice out of range.'
      continue
    }
    $node = $SelectedNodes[$number - 1]
    Set-TestNode $node
    Write-Ok "Selected: $node"
  }
}

Assert-File $ProfilePath 'Mihomo profile'
Assert-File $MihomoPath 'Mihomo executable'
Assert-PortFree $MixedPort 'Mixed proxy'
Assert-PortFree $ControllerPort 'Controller'

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$nodesJson = ConvertTo-JsonArray $Nodes
$configBuilder = @'
import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

profile_path = pathlib.Path(sys.argv[1])
config_path = pathlib.Path(sys.argv[2])
node_regex = sys.argv[3]
explicit_nodes = json.loads(sys.argv[4])
mixed_port = int(sys.argv[5])
controller_port = int(sys.argv[6])
group_name = sys.argv[7]

with profile_path.open("r", encoding="utf-8") as f:
    profile = yaml.safe_load(f)

if not isinstance(profile, dict):
    raise SystemExit("profile is not a YAML object")

proxies = profile.get("proxies")
if not isinstance(proxies, list):
    raise SystemExit("profile has no proxies list")

by_name = {p.get("name"): p for p in proxies if isinstance(p, dict) and p.get("name")}

if explicit_nodes:
    missing = [name for name in explicit_nodes if name not in by_name]
    if missing:
        raise SystemExit("missing node(s): " + ", ".join(missing))
    selected_names = list(explicit_nodes)
else:
    rx = re.compile(node_regex)
    selected_names = [name for name in by_name if rx.search(str(name))]

if not selected_names:
    raise SystemExit("no nodes matched")

selected_proxies = [by_name[name] for name in selected_names]

config = {
    "mixed-port": mixed_port,
    "allow-lan": False,
    "mode": "rule",
    "log-level": "warning",
    "external-controller": f"127.0.0.1:{controller_port}",
    "secret": "",
    "proxies": selected_proxies,
    "proxy-groups": [
        {
            "name": group_name,
            "type": "select",
            "proxies": selected_names,
        }
    ],
    "rules": [f"MATCH,{group_name}"],
}

if isinstance(profile.get("dns"), dict):
    config["dns"] = profile["dns"]

with config_path.open("w", encoding="utf-8", newline="\n") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

print(json.dumps(selected_names, ensure_ascii=True))
'@

Write-Info "Building temporary Mihomo config from $ProfilePath"
$pyOutput = & $Python -c $configBuilder $ProfilePath $ConfigPath $NodeNameRegex $nodesJson $MixedPort $ControllerPort $GroupName 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Failed to build temporary config: $($pyOutput -join ' ')"
}

$pyOutputLines = @($pyOutput)
$selectedNodes = @($pyOutputLines[-1] | ConvertFrom-Json)
Write-Ok ("Selected nodes: " + ($selectedNodes -join ', '))
Write-Info "Temporary config: $ConfigPath"

Write-Info "Checking temporary Mihomo config"
$checkOutput = & $MihomoPath -t -d $RunDir -f $ConfigPath 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Mihomo config check failed: $($checkOutput -join ' ')"
}
Write-Ok 'Mihomo config check passed'

$mihomoProcess = $null
$allResults = New-Object System.Collections.Generic.List[object]
$controllerDelays = @{}

try {
  Write-Info "Starting temporary Mihomo core on mixed-port $MixedPort and controller $ControllerPort"
  $mihomoProcess = Start-Process -FilePath $MihomoPath `
    -ArgumentList @('-d', $RunDir, '-f', $ConfigPath) `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $MihomoStdoutPath `
    -RedirectStandardError $MihomoStderrPath

  Start-Sleep -Milliseconds 800
  if ($mihomoProcess.HasExited) {
    throw "Mihomo exited early. Check $MihomoStderrPath"
  }
  Wait-ControllerReady
  Write-Ok 'Temporary Mihomo controller is ready'

  if ($ManualMenu) {
    Show-ManualMenu $selectedNodes
  }

  Write-Info "Controller delay is recorded only as a reference; judge real latency by TTFB/total/jitter."

  foreach ($node in $selectedNodes) {
    Write-Host ''
    Write-Info "Testing node: $node"
    Set-TestNode $node
    Start-Sleep -Milliseconds $PauseBetweenNodesMs

    $controllerDelay = Get-ControllerDelay $node
    $controllerDelays[$node] = $controllerDelay
    if ($null -ne $controllerDelay) {
      Write-Info "Controller delay reference: $controllerDelay ms"
    } else {
      Write-Warn2 'Controller delay reference failed.'
    }

    if ($ManualHoldSeconds -gt 0) {
      Write-Info "Manual hold: use local proxy 127.0.0.1:$MixedPort for $ManualHoldSeconds seconds."
      Start-Sleep -Seconds $ManualHoldSeconds
    }

    if (-not $SkipAutoMeasure) {
      foreach ($url in $Urls) {
        for ($i = 1; $i -le $Warmup; $i++) {
          $allResults.Add((Invoke-CurlTiming -NodeName $node -Url $url -Iteration $i -IsWarmup $true))
        }
        for ($i = 1; $i -le $Samples; $i++) {
          $result = Invoke-CurlTiming -NodeName $node -Url $url -Iteration $i -IsWarmup $false
          $allResults.Add($result)
          if ($result.ExitCode -eq 0) {
            Write-Host ("  {0} [{1}/{2}] code={3} ttfb={4}ms total={5}ms" -f $url, $i, $Samples, $result.HttpCode, $result.StartTransferMs, $result.TotalMs)
          } else {
            Write-Warn2 ("  {0} [{1}/{2}] curl_exit={3} {4}" -f $url, $i, $Samples, $result.ExitCode, $result.Error)
          }
        }
      }
    }
  }

  if ($SkipAutoMeasure) {
    Write-Warn2 'Auto measurement skipped by -SkipAutoMeasure.'
    return
  }

  $allResults | Export-Csv -LiteralPath $RawCsvPath -NoTypeInformation -Encoding UTF8

  $summaryRows = New-Object System.Collections.Generic.List[object]
  foreach ($node in $selectedNodes) {
    foreach ($url in $Urls) {
      $rows = @($allResults | Where-Object { -not $_.Warmup -and $_.Node -eq $node -and $_.Url -eq $url })
      $okRows = @($rows | Where-Object { $_.ExitCode -eq 0 -and $null -ne $_.StartTransferMs })
      $ttfbValues = @($okRows | ForEach-Object { [double]$_.StartTransferMs })
      $totalValues = @($okRows | ForEach-Object { [double]$_.TotalMs })
      $medianTtfb = Get-Percentile $ttfbValues 50
      $p95Ttfb = Get-Percentile $ttfbValues 95
      $summaryRows.Add([pscustomobject]@{
        Node = $node
        Url = $url
        Samples = $rows.Count
        Ok = $okRows.Count
        Fail = ($rows.Count - $okRows.Count)
        ControllerDelayMs = $controllerDelays[$node]
        MedianTtfbMs = $medianTtfb
        P95TtfbMs = $p95Ttfb
        TtfbJitterMs = if ($null -ne $medianTtfb -and $null -ne $p95Ttfb) { Round-OrNull ($p95Ttfb - $medianTtfb) } else { $null }
        MedianTotalMs = Get-Percentile $totalValues 50
        P95TotalMs = Get-Percentile $totalValues 95
      })
    }
  }

  $overallRows = New-Object System.Collections.Generic.List[object]
  foreach ($node in $selectedNodes) {
    $rows = @($allResults | Where-Object { -not $_.Warmup -and $_.Node -eq $node })
    $okRows = @($rows | Where-Object { $_.ExitCode -eq 0 -and $null -ne $_.StartTransferMs })
    $ttfbValues = @($okRows | ForEach-Object { [double]$_.StartTransferMs })
    $totalValues = @($okRows | ForEach-Object { [double]$_.TotalMs })
    $medianTtfb = Get-Percentile $ttfbValues 50
    $p95Ttfb = Get-Percentile $ttfbValues 95
    $overallRows.Add([pscustomobject]@{
      Node = $node
      Samples = $rows.Count
      Ok = $okRows.Count
      Fail = ($rows.Count - $okRows.Count)
      ControllerDelayMs = $controllerDelays[$node]
      MedianTtfbMs = $medianTtfb
      P95TtfbMs = $p95Ttfb
      TtfbJitterMs = if ($null -ne $medianTtfb -and $null -ne $p95Ttfb) { Round-OrNull ($p95Ttfb - $medianTtfb) } else { $null }
      MedianTotalMs = Get-Percentile $totalValues 50
      P95TotalMs = Get-Percentile $totalValues 95
    })
  }

  $summaryRows | Export-Csv -LiteralPath $SummaryCsvPath -NoTypeInformation -Encoding UTF8
  $overallRows | Export-Csv -LiteralPath $OverallCsvPath -NoTypeInformation -Encoding UTF8

  Write-Host ''
  Write-Ok "Raw results: $RawCsvPath"
  Write-Ok "Per-URL summary: $SummaryCsvPath"
  Write-Ok "Overall summary: $OverallCsvPath"
  Write-Host ''
  Write-Host 'Overall summary, sorted by median TTFB:'
  $overallRows | Sort-Object MedianTtfbMs | Format-Table Node, Ok, Fail, ControllerDelayMs, MedianTtfbMs, P95TtfbMs, TtfbJitterMs, MedianTotalMs, P95TotalMs -AutoSize
  Write-Host ''
  Write-Host 'Per-URL summary, sorted by URL then median TTFB:'
  $summaryRows | Sort-Object Url, MedianTtfbMs | Format-Table Url, Node, Ok, Fail, MedianTtfbMs, P95TtfbMs, TtfbJitterMs, MedianTotalMs -AutoSize
} finally {
  if ($null -ne $mihomoProcess -and -not $mihomoProcess.HasExited) {
    if ($KeepCoreAlive) {
      Write-Warn2 "Temporary Mihomo core is still running. PID=$($mihomoProcess.Id), proxy=127.0.0.1:$MixedPort, controller=127.0.0.1:$ControllerPort"
    } else {
      Stop-Process -Id $mihomoProcess.Id -Force
      Write-Info 'Temporary Mihomo core stopped.'
    }
  }
}
