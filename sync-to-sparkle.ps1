<#
.SYNOPSIS
  把 creds.local.js + main.js 拼接为 inline 脚本，dry-run 输出到 _staging\，或覆盖 Sparkle 生产 override 文件。

.DESCRIPTION
  解决 Sparkle 多 override 脚本独立 JS sandbox 不共享 globalThis 的问题：
  让凭据 IIFE 与业务 main(config) 处于同一文件、同一 sandbox，getCred() 能直接读到 __creds。

  默认行为是 dry-run（输出到 _staging\19d8b14dfd4.js.test，不动生产文件）。
  使用 -Production 才会真正覆盖 Sparkle override 目录里的 19d8b14dfd4.js。

.PARAMETER Production
  覆盖 Sparkle 生产文件 D:\scoop\apps\sparkle\current\data\override\19d8b14dfd4.js。
  默认 false（dry-run，输出到 _staging\）。

.PARAMETER Pull
  在拼接前先 git pull --ff-only 同步远程。失败只警告，不退出。

.PARAMETER NoBackup
  生产模式下跳过 .bak-<timestamp> 备份步骤。不推荐。

.PARAMETER Watch
  常驻模式：循环 git pull → 比较 main.js SHA256 → 变化才执行 sync。必须配合 -Production，
  否则报错退出（dry-run + watch 没意义，永远没人读 _staging 输出）。Ctrl+C 退出。

.PARAMETER WatchInterval
  -Watch 模式下两次循环之间的休眠秒数。默认 1800（30 分钟），建议不要低于 300。

.EXAMPLE
  .\sync-to-sparkle.ps1
  # dry-run：输出到 _staging\19d8b14dfd4.js.test

.EXAMPLE
  .\sync-to-sparkle.ps1 -Production
  # 覆盖 Sparkle 生产文件，自动备份

.EXAMPLE
  .\sync-to-sparkle.ps1 -Production -Pull
  # 先 git pull 拿别处 push 的 main.js 最新版，再覆盖生产

.EXAMPLE
  .\sync-to-sparkle.ps1 -Production -Watch
  # 常驻：每 30 分钟 git pull 一次，main.js 有变更才覆盖生产。Ctrl+C 退出。

.NOTES
  脚本路径硬要求：必须放在仓库根（与 main.js / creds.local.js 同目录）。
#>

[CmdletBinding()]
param(
  [switch]$Production,
  [switch]$Pull,
  [switch]$NoBackup,
  [switch]$Watch,
  [int]$WatchInterval = 1800
)

$ErrorActionPreference = 'Stop'

# ----------------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------------

$RepoRoot   = $PSScriptRoot
$CredsFile  = Join-Path $RepoRoot 'creds.local.js'
$MainFile   = Join-Path $RepoRoot 'main.js'
$StagingDir = Join-Path $RepoRoot '_staging'
$StagingOut = Join-Path $StagingDir '19d8b14dfd4.js.test'
$ProdFile   = 'D:\scoop\apps\sparkle\current\data\override\19d8b14dfd4.js'

# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------

function Write-Ok    ($msg) { Write-Host "[ OK ] $msg" -ForegroundColor Green }
function Write-Info  ($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Warn2 ($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err   ($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }

# ----------------------------------------------------------------------------
# Watch 前置校验（必须配 -Production；dry-run + watch 没意义）
# ----------------------------------------------------------------------------

if ($Watch -and -not $Production) {
  Write-Err "-Watch 必须配合 -Production 使用（dry-run 模式下 watch 永远没人读 _staging 输出）"
  exit 1
}
if ($Watch -and $WatchInterval -lt 5) {
  Write-Err "-WatchInterval 不能 < 5 秒（避免拖死 git / Sparkle）"
  exit 1
}

# ----------------------------------------------------------------------------
# Invoke-SyncOnce：执行一次完整 sync（前置检查 + 可选 git pull + 拼接 + 写入 + 摘要）
# 在 -Watch 模式下被循环调用；非 watch 模式调用一次。
# ----------------------------------------------------------------------------

function Invoke-SyncOnce {

# ----------------------------------------------------------------------------
# 1. 前置检查
# ----------------------------------------------------------------------------

Write-Info "仓库根: $RepoRoot"

if (-not (Test-Path $CredsFile)) {
  Write-Err "找不到 creds.local.js — 请按 README 复制 creds.local.example.js 并填入真凭据"
  exit 1
}
if ((Get-Item $CredsFile).Length -le 0) {
  Write-Err "creds.local.js 为空文件"
  exit 1
}

if (-not (Test-Path $MainFile)) {
  Write-Err "找不到 main.js"
  exit 1
}
if ((Get-Item $MainFile).Length -le 0) {
  Write-Err "main.js 为空文件"
  exit 1
}

Write-Ok "creds.local.js + main.js 均存在且非空"

# ----------------------------------------------------------------------------
# 2. 可选 git pull
# ----------------------------------------------------------------------------

$gitCommit = '<unknown>'
if ($Pull) {
  Write-Info "执行 git pull --ff-only..."
  try {
    Push-Location $RepoRoot
    git pull --ff-only 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
      Write-Warn2 "git pull 返回非零（继续 sync，但远程未同步）"
    } else {
      Write-Ok "git pull 完成"
    }
  } catch {
    Write-Warn2 "git pull 异常：$($_.Exception.Message)（继续 sync）"
  } finally {
    Pop-Location
  }
}

try {
  Push-Location $RepoRoot
  $rev = git rev-parse --short HEAD 2>$null
  if ($LASTEXITCODE -eq 0 -and $rev) { $gitCommit = $rev.Trim() }
} catch {
  # 不在 git 仓库里也没关系
} finally {
  Pop-Location
}

# ----------------------------------------------------------------------------
# 3. 拼接
# ----------------------------------------------------------------------------

$timestamp  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
$header = @"
/**
 * !!! AUTO-GENERATED — DO NOT EDIT BY HAND !!!
 *
 * 由 sync-to-sparkle.ps1 拼接生成（creds.local.js + main.js）。
 * 任何手工改动在下次 sync 后都会丢失。
 *
 * 生成时间: $timestamp
 * git commit: $gitCommit
 * 源文件:
 *   - creds.local.js (本地，不入 git)
 *   - main.js (远程: github.com/konbakuyomu/frontier-chain-skeleton)
 */

"@

# 显式按 UTF-8 读，避开 PowerShell 5.1 默认按 ANSI/CP936 读 .js 文件导致中文 mojibake
$credsContent = [System.IO.File]::ReadAllText($CredsFile, [System.Text.Encoding]::UTF8)
$mainContent  = [System.IO.File]::ReadAllText($MainFile,  [System.Text.Encoding]::UTF8)

$combined = $header + $credsContent + "`r`n`r`n" + $mainContent

# ----------------------------------------------------------------------------
# 4. 写入目标
# ----------------------------------------------------------------------------

if ($Production) {
  Write-Info "目标模式: PRODUCTION → $ProdFile"

  if (-not (Test-Path $ProdFile)) {
    Write-Warn2 "生产文件不存在（首次写入？）"
  } else {
    if (-not $NoBackup) {
      $bakStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
      $bakFile  = "$ProdFile.bak-$bakStamp"
      Copy-Item -Path $ProdFile -Destination $bakFile -Force
      Write-Ok "已备份: $bakFile"
    } else {
      Write-Warn2 "已跳过备份（-NoBackup）"
    }
  }

  $prodDir = Split-Path -Parent $ProdFile
  if (-not (Test-Path $prodDir)) {
    Write-Err "Sparkle override 目录不存在: $prodDir（Sparkle 安装路径异常？）"
    exit 1
  }

  # 用 .NET API 写 UTF-8 无 BOM（PowerShell 5.1 -Encoding UTF8 会带 BOM）
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($ProdFile, $combined, $utf8NoBom)
  $finalPath = $ProdFile
  Write-Ok "已写入生产文件"
} else {
  Write-Info "目标模式: DRY-RUN → $StagingOut"

  if (-not (Test-Path $StagingDir)) {
    New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($StagingOut, $combined, $utf8NoBom)
  $finalPath = $StagingOut
  Write-Ok "已写入 staging"
}

# ----------------------------------------------------------------------------
# 5. 验证摘要
# ----------------------------------------------------------------------------

$finalText  = [System.IO.File]::ReadAllText($finalPath, [System.Text.Encoding]::UTF8)
$finalLines = ($finalText -split "`r?`n").Count
$finalSize  = [math]::Round((Get-Item $finalPath).Length / 1KB, 2)

function Count-Match($text, $pattern) {
  return [regex]::Matches($text, $pattern).Count
}

$creds_decl    = Count-Match $finalText 'globalThis\.__creds'
$main_export   = Count-Match $finalText 'globalThis\.main\s*=\s*main'
# getCred 含 getCredInt（这俩都是凭据查询入口）
$getCred_calls = Count-Match $finalText 'getCred(Int)?\s*\('
$has_scrapegw  = Count-Match $finalText 'scrapegw_host'
$has_frontier  = Count-Match $finalText 'frontier_server'
$has_vps       = Count-Match $finalText 'vps_server'

Write-Host ""
Write-Host "===== 验证摘要 =====" -ForegroundColor Magenta
Write-Host ("  路径: {0}" -f $finalPath)
Write-Host ("  大小: {0} KB" -f $finalSize)
Write-Host ("  行数: {0}" -f $finalLines)
$colCreds    = if ($creds_decl    -ge 1)  { 'Green' } else { 'Red' }
$colMain     = if ($main_export   -ge 1)  { 'Green' } else { 'Red' }
$colGetCred  = if ($getCred_calls -ge 12) { 'Green' } else { 'Yellow' }
$colScrape   = if ($has_scrapegw  -ge 1)  { 'Green' } else { 'Red' }
$colFrontier = if ($has_frontier  -ge 1)  { 'Green' } else { 'Red' }
$colVps      = if ($has_vps       -ge 1)  { 'Green' } else { 'Red' }

Write-Host ("  globalThis.__creds 命中: {0}" -f $creds_decl)        -ForegroundColor $colCreds
Write-Host ("  globalThis.main = main 命中: {0}" -f $main_export)    -ForegroundColor $colMain
Write-Host ("  getCred(...) 调用数: {0}" -f $getCred_calls)          -ForegroundColor $colGetCred
Write-Host ("  scrapegw_host 命中: {0}" -f $has_scrapegw)            -ForegroundColor $colScrape
Write-Host ("  frontier_server 命中: {0}" -f $has_frontier)          -ForegroundColor $colFrontier
Write-Host ("  vps_server 命中: {0}" -f $has_vps)                    -ForegroundColor $colVps

Write-Host ""
if ($Production) {
  Write-Ok "完成。下一步：Sparkle UI → 当前订阅 → 刷新订阅"
  Write-Info "刷新后日志应见 [skeleton] 新增规则目标校验通过, 共 N 条"
  Write-Info "刷新后日志不应见 [skeleton] 未检测到任何凭据注入通道"
} else {
  Write-Ok "完成（dry-run）。下一步可对比："
  Write-Host "  code --diff `"$finalPath`" `"$ProdFile`"" -ForegroundColor Gray
  Write-Host "  # 或 git diff --no-index `"$ProdFile`" `"$finalPath`"" -ForegroundColor Gray
  Write-Info "确认无误后跑：.\sync-to-sparkle.ps1 -Production"
}

}  # end function Invoke-SyncOnce

# ----------------------------------------------------------------------------
# 主流程：单次 vs Watch 循环
# ----------------------------------------------------------------------------

if (-not $Watch) {
  Invoke-SyncOnce
  exit 0
}

# Watch 模式：循环 git pull → SHA256 比较 → 变化才 sync
Write-Info "进入 Watch 模式（间隔 $WatchInterval 秒，Ctrl+C 退出）"

# 强制开启 -Pull（watch 的全部价值就是跟远程，不 pull 等于死循环）
$script:Pull = $true

function Get-MainSha256 {
  if (-not (Test-Path $MainFile)) { return '<missing>' }
  return (Get-FileHash -Path $MainFile -Algorithm SHA256).Hash
}

# 启动时先记一次哈希，但不强制 sync —— 由用户首次手动 -Production 兜底
$lastHash = Get-MainSha256
Write-Info "初始 main.js SHA256: $lastHash（不立即 sync，等下一轮变化触发）"

while ($true) {
  Write-Host ""
  Write-Info "[watch] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 轮询开始"

  # 1. git pull
  try {
    Push-Location $RepoRoot
    git pull --ff-only 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
      Write-Warn2 "[watch] git pull 返回非零，本轮跳过 sync"
    }
  } catch {
    Write-Warn2 "[watch] git pull 异常：$($_.Exception.Message)，本轮跳过 sync"
  } finally {
    Pop-Location
  }

  # 2. SHA256 比较
  $currHash = Get-MainSha256
  if ($currHash -eq $lastHash) {
    Write-Info "[watch] main.js 未变化（SHA256 未变）→ sleep $WatchInterval s"
  } else {
    Write-Ok "[watch] main.js 变化：$lastHash → $currHash，触发 sync"
    try {
      Invoke-SyncOnce
      $lastHash = $currHash
    } catch {
      Write-Err "[watch] sync 异常：$($_.Exception.Message)（保留旧 hash，下轮重试）"
    }
  }

  Start-Sleep -Seconds $WatchInterval
}
