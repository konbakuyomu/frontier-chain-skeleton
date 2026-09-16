<#
.SYNOPSIS
  Check or migrate only Mihomo's AI service selector through Sparkle's named pipe.

.DESCRIPTION
  The default is read-only. Use -Apply only after the refreshed profile declares
  the residential selector as the first AI option. The script writes one proxy
  group, reads it back, and attempts to restore the previous choice if read-back
  verification fails.
#>

[CmdletBinding()]
param(
  [switch]$Apply,
  [string]$PipeName = 'Sparkle\mihomo',
  [string]$GroupName = 'AI服务',
  [string]$ExpectedChoice = '🏡 家宽选择',
  [int]$ConnectTimeoutMs = 3000
)

$ErrorActionPreference = 'Stop'

function Invoke-MihomoPipeRequest {
  param(
    [ValidateSet('GET', 'PUT')][string]$Method,
    [string]$Path,
    [object]$BodyObject
  )

  $pipe = [System.IO.Pipes.NamedPipeClientStream]::new(
    '.',
    $PipeName,
    [System.IO.Pipes.PipeDirection]::InOut,
    [System.IO.Pipes.PipeOptions]::Asynchronous
  )
  try {
    $pipe.Connect($ConnectTimeoutMs)
    $bodyBytes = [byte[]]@()
    $headers = @(
      "$Method $Path HTTP/1.1",
      'Host: localhost',
      'Connection: close'
    )
    if ($null -ne $BodyObject) {
      $bodyJson = $BodyObject | ConvertTo-Json -Compress
      $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($bodyJson)
      $headers += 'Content-Type: application/json; charset=utf-8'
      $headers += "Content-Length: $($bodyBytes.Length)"
    }
    $headerBytes = [System.Text.Encoding]::ASCII.GetBytes(($headers -join "`r`n") + "`r`n`r`n")
    $pipe.Write($headerBytes, 0, $headerBytes.Length)
    if ($bodyBytes.Length -gt 0) { $pipe.Write($bodyBytes, 0, $bodyBytes.Length) }
    $pipe.Flush()

    $memory = [System.IO.MemoryStream]::new()
    try {
      $buffer = New-Object byte[] 8192
      while (($read = $pipe.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $memory.Write($buffer, 0, $read)
      }
      $response = [System.Text.Encoding]::UTF8.GetString($memory.ToArray())
    } finally {
      $memory.Dispose()
    }
  } finally {
    $pipe.Dispose()
  }

  $parts = $response -split "`r`n`r`n", 2
  $statusLine = ($parts[0] -split "`r`n", 2)[0]
  if ($statusLine -notmatch '^HTTP/1[.][01] ([0-9]{3})') { throw 'invalid Mihomo controller response' }
  $statusCode = [int]$Matches[1]
  if ($statusCode -lt 200 -or $statusCode -ge 300) { throw "Mihomo controller returned HTTP $statusCode" }
  $body = if ($parts.Count -gt 1) { $parts[1] } else { '' }
  return [pscustomobject]@{ StatusCode = $statusCode; Body = $body }
}

function Get-AiGroupState {
  $encodedGroup = [Uri]::EscapeDataString($GroupName)
  $response = Invoke-MihomoPipeRequest -Method GET -Path "/proxies/$encodedGroup" -BodyObject $null
  if (-not $response.Body) { throw 'Mihomo controller returned an empty AI group body' }
  $state = $response.Body | ConvertFrom-Json
  if ($state.name -ne $GroupName -or -not $state.all) { throw 'Mihomo controller AI group shape mismatch' }
  return $state
}

function Set-AiGroupChoice([string]$Choice) {
  $encodedGroup = [Uri]::EscapeDataString($GroupName)
  Invoke-MihomoPipeRequest -Method PUT -Path "/proxies/$encodedGroup" -BodyObject @{ name = $Choice } | Out-Null
}

function Get-ChoiceState([object]$Choice) {
  $value = [string]$Choice
  if ($value -eq $ExpectedChoice) { return 'expected' }
  if ([string]::IsNullOrWhiteSpace($value)) { return 'empty' }
  return 'other'
}

$before = Get-AiGroupState
$available = @($before.all) -contains $ExpectedChoice
if (-not $available) { throw "AI group does not expose the expected choice: $ExpectedChoice" }
$declaredFirst = @($before.all)[0] -eq $ExpectedChoice

if (-not $Apply) {
  [pscustomobject]@{
    group = $GroupName
    now_state = Get-ChoiceState $before.now
    now_is_expected = $before.now -eq $ExpectedChoice
    expected_available = $available
    declared_first = $declaredFirst
    migration_required = $before.now -ne $ExpectedChoice
    mode = 'check-only'
  } | ConvertTo-Json -Compress
  exit 0
}

if (-not $declaredFirst) {
  throw 'AI group does not declare the expected selector as its first proxy option'
}

if ($before.now -eq $ExpectedChoice) {
  [pscustomobject]@{ group = $GroupName; now_is_expected = $true; changed = $false; read_back = $true } | ConvertTo-Json -Compress
  exit 0
}

$previousChoice = [string]$before.now
try {
  Set-AiGroupChoice $ExpectedChoice
  $after = Get-AiGroupState
  if ($after.now -ne $ExpectedChoice) { throw 'AI selector read-back did not match the requested choice' }
  [pscustomobject]@{
    group = $GroupName
    previous_state = Get-ChoiceState $previousChoice
    now_is_expected = $true
    changed = $true
    read_back = $true
  } | ConvertTo-Json -Compress
} catch {
  if ($previousChoice -and (@($before.all) -contains $previousChoice)) {
    try { Set-AiGroupChoice $previousChoice } catch { }
  }
  throw
}
