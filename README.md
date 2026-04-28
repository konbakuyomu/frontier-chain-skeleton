# frontier-chain-skeleton

通用 mihomo 全 config 覆写脚本，由 GitHub 公开仓库统一发布，供 Sparkle (Win/Mac) + Sub-Store (Linux/VPS) 双端通过 jsdelivr CDN 共享引用。

> 单点修改：仓库改一行 → `git push` → 10 分钟内 Sparkle 与 Sub-Store 各自刷新订阅都能拉到新版（jsdelivr 缓存窗口）。

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

- Sparkle override 兼容（`type: remote` + `ext: js`）
- Sub-Store mihomoProfile 文件类型兼容（backend 内部会调用 `globalThis.main(config)`）
- iPhone Shadowrocket 不直接调脚本——它订阅的是 Sub-Store 输出的 mihomo YAML

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

### Sparkle (Windows / mac)

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

1. Files → 新建 mihomoProfile 文件
2. 选定 sourceType=collection（合并双订阅 bitbyte ccrui + inetsnode）
3. 远程脚本 URL: `https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@main/main.js`
4. mihomoProfile 的敏感参数放在 Sub-Store 本地 Script Operator arguments，不要拼进公开 URL。

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

- 配置用 `policy-regex-filter=Frontier|🏠` 自动捕获节点订阅里的家宽节点，无须手维护节点列表
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

## Sparkle 端使用方式（Windows）

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
