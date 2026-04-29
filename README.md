# frontier-chain-skeleton

通用 mihomo 全 config 覆写脚本，由 GitHub 公开仓库统一发布，当前主链是在 VPS/Sub-Store 侧生成最终 mihomo YAML，再发给 Sparkle (Windows) 和 FlClash (Android) 直接订阅。

> 单点修改：仓库改一行 → `git push` → VPS/Sub-Store 处理链刷新 → Sparkle / FlClash 刷新最终订阅即可生效。旧 Sparkle 本地覆写脚本和 FlClash 合并脚本只作为回滚路径保留。

## 功能

- 注入 N 个家宽链式代理节点（机场→家宽 dialer-proxy / VPS→家宽 服务端链）
- 强制覆盖 DNS 配置防泄露（fake-ip + 海外 DoH + respect-rules）
- 注入完整 AI 服务分流规则（Anthropic / OpenAI / Google AI / livekit / arkose / turnstile / Azure CDN / NTP，50+ 条）
- 自动补 browserleaks / dnsleaktest 测试站点规则、googleusercontent / gstatic 扩展规则

## 入口约定

```javascript
function main(config) { /* 修改并返回 config */ }
if (typeof globalThis !== 'undefined') globalThis.main = main;
```

- Sub-Store mihomoProfile / File 处理链兼容（backend 内部会调用 `globalThis.main(config)`）
- Sparkle / FlClash 主链不再直接调用脚本，而是订阅 Sub-Store 输出的最终 mihomo YAML
- iPhone Shadowrocket 不订阅完整 mihomo YAML，继续使用配置订阅 + 节点订阅双订阅模型

## 必需参数（通过 $arguments 注入）

| key | 说明 | 必填 | 示例 |
|---|---|---|---|
| `scrapegw_host` | ScrapeGW 住宅池入口 host | 否（不填 ScrapeGW 节点失效，不影响 Frontier 主链） | `<provider-host>` |
| `scrapegw_port` | ScrapeGW 端口 | 否 | `<port>` |
| `scrapegw_user` | ScrapeGW sticky session 用户名（含 country/state/session/lifetime） | 否 | `<sticky-session-user>` |
| `scrapegw_pass` | ScrapeGW 密码 | 否 | `<password>` |
| `frontier_server` | Frontier 家宽入口 host（暴露后他人可扫端口/IP 攻击，故不进仓库） | 是 | `<frontier-host>` |
| `frontier_port` | Frontier 家宽入口端口 | 否（默认 `1145`） | `<port>` |
| `frontier_password` | Frontier 家宽 SS 密码 | 是 | `<password>` |
| `frontier_cipher` | Frontier 家宽 SS 加密 | 否（默认 `chacha20-ietf-poly1305`） | `chacha20-ietf-poly1305` |
| `vps_server` | VPS 服务端链路入口 IP/host | 是（用 VPS 兜底） | `<vps-ip-or-host>` |
| `vps_port` | VPS 端口 | 否（默认 `51388`） | `<port>` |
| `vps_password` | VPS SS 密码 | 是 | `<password>` |
| `vps_cipher` | VPS SS 加密 | 否（默认 `chacha20-ietf-poly1305`） | `chacha20-ietf-poly1305` |

> 任何凭据都**不会**进入仓库。本仓库的搜索结果中**应**为 0 命中（CI 可加 grep 校验）。

## 使用方式

### 当前主链：VPS/Sub-Store 生成最终 mihomo 订阅

处理链固定为：

```text
merged-airports Collection
  → powerfullz/override-rules convert.min.js
  → frontier-chain-skeleton/main.js
  → final mihomo YAML
```

客户端使用方式：

- Sparkle：新增或切换到最终 mihomo 订阅 profile，停用本地 global overrides 作为主链。
- FlClash：新增或切换到同一最终 mihomo 订阅 profile，覆写模式选 Standard / None，不绑定脚本。
- Shadowrocket：继续用 `shadowrocket.conf` 配置订阅 + `merged-airports?target=ShadowRocket` 节点订阅。

每个上游订阅进入 Collection 前先挂 `substore-source-marker.js`，用本地 arguments 传 `source_prefix`：

| 上游订阅 | source_prefix |
|---|---|
| `ccrui` | `CCR` |
| `kuma` | `KUMA` |

节点命名由 Sub-Store Collection 的 `shadowrocket-nodes-injector.js` 统一完成，普通节点格式为：

```text
CCR | 美国-DP-广东
KUMA | 日本-绿云-软银-深港
KUMA | 美国-Frontier-家宽-链式
```

链路节点固定保留：

```text
🏠 [VPS→家宽] Frontier
```

### VPS/Sub-Store 标准维护流程

本仓库是**非敏感源码层**的唯一入口；VPS 上的 `sub-store.json` 是运行态，里面可能包含 token、机场订阅 URL、Script Operator arguments 等敏感内容，不进 Git。

日常维护按这个流程走：

```powershell
# 1. 本地只读验证：JS/Python 语法 + VPS 运行态检查
.\scripts\verify-substore.ps1 `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>

# 2. 改脚本后先 dry-run，不改 VPS
.\scripts\deploy-substore.ps1

# 3. 确认无误后才发布到 VPS
.\scripts\deploy-substore.ps1 -Apply `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>
```

脚本边界：

| 脚本 | 默认行为 | 作用 |
|---|---|---|
| `scripts/verify-substore.ps1` | 只读 | 本地语法检查 + VPS Sub-Store 结构/输出检查，不打印敏感值 |
| `scripts/deploy-substore.ps1` | dry-run | 把本仓库脚本发布到 VPS Sub-Store；只有加 `-Apply` 才会修改远端 |
| `scripts/restore-substore-backup.ps1` | 只列备份 | 列出或恢复 VPS `backups/` 里的 `sub-store.json` 备份；只有加 `-Apply -BackupName` 才恢复 |
| `scripts/update-powerfullz-inline.py` | VPS 上运行 | 拉取 powerfullz 最新脚本，内联到 `frontier-chain-mihomo` 第一段 Script Operator |

维护入口：

| 要改什么 | 改哪里 | 发布目标 |
|---|---|---|
| 节点来源前缀 | `substore-source-marker.js` | 上游 subscription 的 source marker Script Operator |
| 节点命名 / 过滤 / `VPS→家宽` 注入 | `shadowrocket-nodes-injector.js` | `merged-airports` Collection Script Operator |
| DNS / AI / 家宽链式 / final mihomo | `main.js` | `frontier-chain-mihomo` 的自定义 Script Operator |
| powerfullz 更新逻辑 | `scripts/update-powerfullz-inline.py` | VPS `/opt/1panel/apps/sub-store/sub-store/update-powerfullz-inline.py` |

发布脚本会保留 Sub-Store 里已有的 `arguments`，不会把凭据从 VPS 拉回仓库。每次真正修改 `sub-store.json` 前会在 VPS `backups/` 下创建时间戳备份。

回滚流程：

```powershell
# 先只列出最近备份，不改远端
.\scripts\restore-substore-backup.ps1 `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>

# 选定备份后才恢复；恢复前会再备份当前 sub-store.json
.\scripts\restore-substore-backup.ps1 `
  -BackupName sub-store.json.bak-deploy-YYYYMMDD-HHMMSS `
  -Apply `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>
```

恢复完成后必须再跑 `verify-substore.ps1`，通过后再让客户端刷新订阅。

### Sparkle 本地覆写回滚路径（Windows / mac）

> 以下是旧方案和回滚方案。日常主链不再推荐让 Sparkle 本地串行跑 powerfullz + 本脚本。

`override.yaml` 改造（参考同仓库 `examples/sparkle-local-patch/override.yaml.example`）：

```yaml
items:
  # 上游 powerfullz（保持不变）
  - id: 19ab525c4c3
    name: powerfullz/override-rules
    type: remote
    ext: js
    url: https://cdn.jsdelivr.net/gh/powerfullz/override-rules/convert.min.js
    global: true

  # 本地凭据 patch（必须排在远程骨架之前——先注入 globalThis.__creds）
  - id: 1a0_local_patch
    name: frontier-chain local creds patch
    type: local
    ext: js
    global: true

  # 远程骨架（jsdelivr）
  - id: 1a0_skeleton_remote
    name: frontier-chain-skeleton (remote)
    type: remote
    ext: js
    url: https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@main/main.js
    global: true
```

Sparkle override 目录里同时放 `1a0_local_patch.js`（见同仓库 `examples/sparkle-local-patch/`），运行时它写入 `globalThis.__creds`，远程骨架通过 `getCred()` 读到。

### Sub-Store (VPS)

1. Files → 新建或维护最终 mihomo profile 文件
2. 选定 sourceType=collection，来源为 `merged-airports`
3. 处理链先挂 `powerfullz/override-rules`，再挂 `frontier-chain-skeleton/main.js`
4. mihomo profile 的敏感参数放在 Sub-Store 本地 Script Operator arguments，不要拼进公开 URL。

```
https://your-substore-domain/<api-prefix>/api/file/<filename>?target=mihomo
```

> Sub-Store 服务端脚本不能依赖客户端订阅 URL fragment。敏感参数应放在后台本地 arguments 中，避免进入 GitHub、jsdelivr、iPhone 订阅 URL。

### iPhone Shadowrocket（双订阅模型）

> **2026-04-28 架构变更**：iPhone 端**不再**走 Sub-Store mihomoProfile YAML 路线。实测 Shadowrocket 对 mihomo 大量字段不识别（GEOSITE 失效、url-test 错选家宽节点、SELECT 组 currentSelection 漂移）。改为**配置和节点解耦**的双订阅模型：

#### 1. 配置订阅（无凭据，公开 jsdelivr）

打开 Shadowrocket → 配置 → 右上 + → 粘贴：

```
https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@main/shadowrocket.conf
```

下载完成后设为使用中。

#### 2. 节点订阅（凭据留在 Sub-Store 本地 arguments）

Sub-Store 后台先建：

1. Subscriptions 添加双机场订阅（如 bitbyte ccrui + kuma/inetsnode）
2. Collections 创建 `merged-airports` 合并两个订阅
3. Files 新建 ShadowRocket 文件，sourceType=collection 引用 `merged-airports`
4. Process Operator 添加 Script，使用 **normal / inline** 模式粘贴脚本正文：
   ```
   https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@main/shadowrocket-nodes-injector.js
   ```
   这个 URL 只用于复制脚本正文和记录来源；Sub-Store v2.22.8 的 link-mode 不会把 UI `arguments` 传给远程脚本。
5. Script Operator arguments 填入 `vps_server` / `vps_port` / `vps_password` / `vps_cipher`。

打开 Shadowrocket → 首页 → 订阅 → + → 粘贴：

```
https://<sub-store-host>/<api-prefix>/download/collection/merged-airports?target=ShadowRocket
```

刷新订阅，确认节点列表中含 `🏠 [VPS→家宽] Frontier`。

#### 关键设计

- 配置用 `policy-regex-filter=\[VPS→家宽\]` 自动捕获节点订阅里的家宽节点，无须手维护节点列表
- 自动选择 / 国家分组用 negative lookahead 排除链路节点，避免 url-test 把家宽节点选成"最快"
- AI 服务（Claude/ChatGPT/Codex/Gemini）默认全部走家宽链路（出口 47.147.31.31）
- `ai-extensions.list` 排在 LingJingMaster `Google.list` / `AI.list` 之前命中，防止 Gemini 被 Google 组吃掉
- 上游规则跟 LingJingMaster + blackmatrix7（社区维护，无须手维护域名）
- 双订阅各自刷新，互不覆盖

详细操作指南：`examples/shadowrocket-iphone-import-guide.md`

## 安全注意

- 本仓库**不包含任何凭据**。所有密码 / token 都通过 Sub-Store 本地 arguments 或 Sparkle 本地凭据文件注入。
- 任何形式的凭据 commit 到本仓库都是事故 —— 立即 rotate 全部凭据 + force-push 重写历史 + 通知所有引用方。
- Sub-Store 端不要把凭据放进 Script Operator link URL fragment；Sparkle 端凭据放本地 patch 脚本（不进 git）。

## Sparkle 端本地覆写回滚方式（Windows）

> 背景：Sparkle 多个 override 脚本运行在**独立 JS sandbox**，`globalThis` 不跨脚本共享。
> 因此不能用"远程骨架 + 本地 patch 注入 globalThis.__creds"两脚本拆分模式。
> 改为：本地拼接 `creds.local.js + main.js` → 单文件 inline 给 Sparkle，凭据 IIFE 与 main(config) 共享同一 sandbox。
>
> VPS Sub-Store / iPhone 端使用内联脚本正文 + 本地 `$arguments`。Sub-Store v2.22.8 的 link-mode 远程 URL 不会把 UI `arguments` 传给脚本。

### 首次配置

1. 把仓库 clone 到 `D:\Dev\50_Scripts\56_Subscriptions\frontier-chain-skeleton\`
2. 在仓库根目录复制凭据模板，填入真凭据：
   ```powershell
   Copy-Item creds.local.example.js creds.local.js
   # 然后用编辑器打开 creds.local.js 填入真实 SCRAPEGW_RAW / frontier_* / vps_*
   ```
   `creds.local.js` 已被 `.gitignore` 排除，不会进 git。
3. 在 PowerShell 跑 dry-run：
   ```powershell
   cd D:\Dev\50_Scripts\56_Subscriptions\frontier-chain-skeleton
   .\sync-to-sparkle.ps1
   ```
   输出到 `_staging\19d8b14dfd4.js.test`，不动生产文件。
4. 用 VS Code diff 对比 staging 与当前生产文件：
   ```powershell
   code --diff _staging\19d8b14dfd4.js.test D:\scoop\apps\sparkle\current\data\override\19d8b14dfd4.js
   ```
   确认凭据均在顶部 IIFE、`function main(config)` 存在、`globalThis.main = main` 在末尾。
5. 跑生产模式（自动备份现有 `.bak-<timestamp>`）：
   ```powershell
   .\sync-to-sparkle.ps1 -Production
   ```
6. Sparkle UI → 当前订阅 → 刷新订阅，看日志：
   - 应有 `[skeleton] 新增规则目标校验通过, 共 N 条`
   - 不应有 `[skeleton] 未检测到任何凭据注入通道` 警告

### 日常工作流

```powershell
# 改完 main.js 后：
.\sync-to-sparkle.ps1 -Production -Pull   # 先 git pull 拿别处 push 的最新版，再 sync 到 Sparkle
git add main.js
git commit -m "..."
git push                                  # 同步给 VPS Sub-Store / iPhone 通过 jsdelivr 拉新版
```

```powershell
# ScrapeGW session 过期（120min lifetime）：
# 改 creds.local.js 里的 SCRAPEGW_RAW 一行
.\sync-to-sparkle.ps1 -Production         # 30 秒搞定，无需碰 main.js / git
```

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| (none) | | dry-run，输出到 `_staging\` 不动生产 |
| `-Production` | false | 覆盖 Sparkle 生产文件，自动 .bak |
| `-Pull` | false | sync 前先 `git pull --ff-only` |
| `-NoBackup` | false | 生产模式下跳过 .bak（不推荐） |
| `-Watch` | false | 常驻：循环 git pull → SHA256 比较 → 变化才 sync。**必须配合 `-Production`**，否则报错退出 |
| `-WatchInterval <秒>` | 1800 | `-Watch` 间隔秒数。建议 ≥ 300 |

### Watch 模式（自动跟版）

让 Sparkle 端"近自动跟版"——后台常驻，远程 main.js 一旦更新（git push）就在下个轮询周期自动 sync 进 Sparkle override：

```powershell
# 前台跑（Ctrl+C 退出）
.\sync-to-sparkle.ps1 -Production -Watch
```

注册为 Windows 登录后自启的 Scheduled Task：

```powershell
schtasks /create /sc onlogon /tn "frontier-skeleton-watcher" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File D:\Dev\50_Scripts\56_Subscriptions\frontier-chain-skeleton\sync-to-sparkle.ps1 -Production -Watch"
```

行为契约：

- 启动时记录 main.js 当前 SHA256，**不立即 sync**（首次手动 `-Production` 兜底）
- 每轮：`git pull --ff-only` → 计算新 SHA256 → 不变则 `sleep $WatchInterval`，变化才走 `Invoke-SyncOnce`
- pull 失败、sync 失败均不退出循环，下轮重试；只有前置检查失败（凭据/源文件缺失）才退出
- 取消 `-Watch` 必须显式 `-Production`，dry-run + watch 直接报错退出

### 文件清单

| 文件 | 入 git？ | 用途 |
|---|---|---|
| `main.js` | yes | 业务逻辑骨架（被 jsdelivr 公开） |
| `creds.local.example.js` | yes | 凭据模板（占位符） |
| `creds.local.js` | **NO** | 真凭据（本机敏感） |
| `sync-to-sparkle.ps1` | yes | 拼接 + 同步脚本 |
| `_staging/` | **NO** | dry-run 输出目录 |
| `*.bak-*` | **NO** | sync 前自动备份 |

## 许可

MIT
