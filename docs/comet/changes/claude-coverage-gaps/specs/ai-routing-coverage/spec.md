# AI 路由覆盖

## 目的

保证 Mihomo 端（Sparkle / FlClash / OpenClash 旁路由）与 Shadowrocket 端对 AI 服务域名的分流覆盖一致、可追溯，并且上游源发生静默变化时能被门禁拦住。

## 两层的职责划分

AI 分流由两层叠加，两者职责不可互换。

**契约层（内联兜底）** 由 `ai-routing-contract.json` 定义，经 `scripts/render-ai-routing.py` 生成，写入 `main.js` 的 `// AI-ROUTING:BEGIN..END` 块与 `shadowrocket.conf` 的 `# AI-ROUTING:BEGIN..END` 块。它来自契约的 `required` 与带 `emit: true` 的 `conditional`，**不经过上游投影**，因此不受 `shared_not_ai` 排除影响。这一层必须在镜像不可用时仍能独立工作。

**镜像层（上游投影）** 由 `subscription-hub/rule_mirror.py` 从上游源拉取、经 `project_rules()` 投影后发布到 `/rules/*`。它规模更大但依赖上游可用性，且会被 `shared_not_ai` 与 `negative` 控点裁剪。两端通过 `RULE-SET`（Shadowrocket）/ `rule-provider`（Mihomo）引用。

上游源固定为 `v2fly/domain-list-community` 的 `data/anthropic`、`data/openai`、`data/xai`，以及 `DustinWin/ruleset_geodata` 的 `ai.list`（社区补充）。`main.js` 另有一组 Gemini 兼容基线，与三服务契约相互独立。

## Claude / Anthropic 覆盖范围

契约 `services.anthropic.required` 持有官方文档明确的无条件必需项：`anthropic.com` 后缀与 `api.anthropic.com`。

契约 `services.anthropic.conditional` 持有按账号或功能条件生效的首方域名，均以 `emit: true` 标记：`claude.ai`、`claude.com`、`claudeusercontent.com`、`claudemcpclient.com`、`claudemcpcontent.com`、`clau.de`、`platform.claude.com`、`mcp-proxy.anthropic.com`、`bridge.claudeusercontent.com`、`downloads.claude.ai`。

由于 `anthropic.com` 与 `claudeusercontent.com` 以后缀形式出现，`console.anthropic.com`、`statsig.anthropic.com`、`*.frame.claudeusercontent.com` 等子域由后缀自动覆盖，无需单列。

`shared_not_ai` 继续排除跨服务共享域名，包括 `b-cdn.net`、`googleapis.com`、`sentry.io`、`statsigapi.net`、`datadoghq.com`、`githubusercontent.com`、`storage.googleapis.com`。这些域名被其他业务共享，纳入 AI 组会造成误伤，因此 `servd-anthropic-website.b-cdn.net` 不进入任何白名单。

## 门禁与哨兵

`rules.json` 中每个 active 逻辑项的 `semantic_required` 是上游哨兵：它逐项断言当前实现必须能从镜像输出中命中该规则。哨兵条目必须来自契约的 `required` 或 `conditional`，不得引用契约之外的域名。

两个 anthropic 逻辑项（`sr-ai-anthropic-v2`、`mihomo-ai-anthropic-v2`）的 `semantic_required` 各含 8 项：`anthropic.com`、`api.anthropic.com`、`claude.ai`、`claude.com`、`claudeusercontent.com`、`claudemcpclient.com`、`claudemcpcontent.com`、`clau.de`。

哨兵必须能拦住上游静默删除：当上游源缺少 `claude.com` 时，`validate_semantic_rules` 必须报缺项并使该项进入 `stale` 或 `failed`，不能因为变化幅度落在 `removed_limit` 内就放行。

发布门禁为 `summary.ok == summary.total` 且 `stale == 0`、`failed == 0`。`stale` 表示保留了上一份可用文件，`failed` 表示首次发布失败且无可用文件；两者都不构成发布成功。

## 两端一致性

`main.js` 与 `shadowrocket.conf` 的 Claude 规则集必须逐条一致，仅策略名与语法形式不同（`${aiGroup}` 对 `🤖 AI 服务`；`DOMAIN-SUFFIX` 对 `DOMAIN-SUFFIX`）。两端各自的 AI 规则块只允许由 `render-ai-routing.py` 改写块内内容，块外文本逐字节不变。

Shadowrocket 的第三方规则一律引用 `<hub>/rules/<file>` 镜像入口，不直接引用 `cdn.jsdelivr.net` 或 GitHub 原始 URL。

## 部署与客户端

规则镜像在 SJC 上经 `subscription-hub` 定时同步，`/rules/*` 为公开机器资源，不要求 Basic Auth。

`shadowrocket.conf` 以 commit-pinned jsdelivr URL 发布，供 Shadowrocket 配置订阅使用。

`main.js` 经 `deploy-substore` 投递到 Sub-Store，由 `frontier-chain-mihomo` 输出给 Sparkle、FlClash 与 OpenClash。

本地 Sparkle 的 override 由仓库根的 `sync-to-sparkle.ps1` 从 `creds.local.js` 与 `main.js` 拼接生成。默认 dry-run 输出到 `_staging\19d8b14dfd4.js.test`；`-Production` 覆盖 `D:\scoop\apps\sparkle\current\data\override\19d8b14dfd4.js` 并保留 `.bak-<timestamp>` 备份。

## 不变量

真实 sync、远端校验与部署只使用既定位点或私有临时目录；任何输出不得包含后端路径、share token、订阅 URL、UUID、密码或私钥。目标仓库中与 AI 路由无关的既有改动不得被回退、覆盖或误提交。`b-cdn.net` 的排除状态保持不变。
