# 目标

补齐 AI 分流中 Claude/Anthropic 的覆盖缺口，把已通过本地门禁的改动提交、部署到 SJC 生产，并更新本地 Sparkle 的 override。

# 范围

本次 change 处理两件事：一是两端内联兜底缺少 `claudemcpclient.com` / `claudemcpcontent.com`，二是 `semantic_required` 哨兵无法覆盖契约 `conditional` 中的首方域名。二者都落在 `frontier-chain-skeleton` 的 AI 路由契约层，改动已完成并通过本地门禁，本 change 负责收口提交、部署与本地 Sparkle 同步。

## Source coverage

| 来源条目与位置 | 读取状态 | 需要保留的内容 | Spec 位置 | 验收 ID | 覆盖状态 | 理由 |
| --- | --- | --- | --- | --- | --- | --- |
| S1：`.trellis/spec/network/shadowrocket-substore-architecture.md` §16 | complete | `main.js` 与 `shadowrocket.conf` 的 AI 规则块由契约生成；两端各需恰好一个 active 逻辑角色；`/rules/*` 不需 Basic Auth | specs/ai-routing-coverage/spec.md「两端内联兜底」 | A1、A2 | covered | 当前有效规格 |
| S2：`ai-routing-contract.json` anthropic 段现状 | complete | `required` 为 `anthropic.com` + `api.anthropic.com`；`conditional` 承载首方 Claude 域名 | specs/ai-routing-coverage/spec.md「契约覆盖」 | A1、A3 | covered | 当前有效契约 |
| S3：`https://code.claude.com/docs/en/network-config` 的 Network access requirements | complete | 官方要求可达 `api.anthropic.com`、`claude.ai`、`claude.com`、`platform.claude.com`、`mcp-proxy.anthropic.com`、`downloads.claude.ai`、`bridge.claudeusercontent.com`、`*.frame.claudeusercontent.com` | specs/ai-routing-coverage/spec.md「契约覆盖」 | A3 | covered | 官方依据 |
| S4：生产 `rules/status.json` 实测 | complete | 生产镜像 `total=38 / ok=38 / stale=0 / failed=0`，逻辑角色两端各 4 | specs/ai-routing-coverage/spec.md「生产部署」 | A7 | covered | 当前运行态 |
| S5：`sync-to-sparkle.ps1` 用法 | complete | `-Production` 覆盖 `D:\scoop\apps\sparkle\current\data\override\19d8b14dfd4.js`，自动备份 | specs/ai-routing-coverage/spec.md「本地客户端」 | A8 | covered | 仓库现有工具 |
| S6：`b-cdn.net` 社区补充条目 | complete | 官方清单未要求 `servd-anthropic-website.b-cdn.net` | — | — | non-goal | 无官方依据，且共享 CDN 排除属有意设计 |

# 非目标

不改 Gemini 兼容基线（`GEMINI_MIHOMO_RULES` 与 `GEMINI_SHADOWROCKET_RULES` 刻意分设，与本需求无关）。不改 `shared_not_ai` 既有排除项，包括 `b-cdn.net`。不引入 `servd-anthropic-website.b-cdn.net`。不清理或重排仓库中与 AI 路由无关的既有改动。不卸载、不停用既有两个 disabled 的 legacy AI 规则项。

# 验收示例

- `main.js` 的 AI 生成块包含 `DOMAIN-SUFFIX,claudemcpclient.com` 与 `DOMAIN-SUFFIX,claudemcpcontent.com`，两条均指向 AI 组。
- `shadowrocket.conf` 的 AI 生成块包含同样两条域名，均指向 `🤖 AI 服务`，且与 `main.js` 的 Claude 规则集逐条一致。
- `ai-routing-contract.json` 的 `services.anthropic.conditional` 覆盖官方 Network access requirements 列出的全部首方域名；`rules.json` 中两个 anthropic 逻辑项的 `semantic_required` 各含 8 项，且每项都来自契约的 `required` 或 `conditional`。
- `render-ai-routing.py --check`、`rule_mirror.py --check`、`pytest test_ai_routing.py` 全部通过，退出码为 0。
- 在私有临时目录执行真实 sync，结果为 `ok=total`、`stale=0`、`failed=0`。
- 上游源缺少 `claude.com` 时，`semantic_required` 哨兵报缺项拦截，不因变化幅度落在 `removed_limit` 内而放行。
- 部署到 SJC 后，生产 `rules/status.json` 为 `ok=total`、`stale=0`、`failed=0`，且镜像文件包含新增的两条域名。
- 本地 Sparkle override 文件 `19d8b14dfd4.js` 已按 `main.js` 重新生成，生成前存在 `.bak-<timestamp>` 备份。

# 约束与不变量

本 change 使用 `current` 隔离，直接在 `main` 分支上工作，不新建分支或 worktree。部署前必须关闭 cc-switch 对 Claude 列的相关改写路径不受影响。真实 sync 与远端校验只在私有临时目录或生产既定位点执行，不打印后端路径、token、订阅 URL、UUID 或密码。`main.js` / `shadowrocket.conf` 只允许由 `render-ai-routing.py` 改写标记块内部，块外文本逐字节不变。目标仓库当前有用户既有的未提交改动与未推送提交，本 change 不得回退、覆盖或顺手提交与 AI 路由无关的内容。

# 决策

D1：`servd-anthropic-website.b-cdn.net` 不加入任何白名单。理由：官方 Network access requirements 未列该域名；`b-cdn.net` 属共享 CDN，其 `shared_not_ai` 排除是为避免误伤其他流量；官方声明的 claude.ai 应用资源来源 `assets-proxy.anthropic.com` 与 `*.claudeusercontent.com` 已被现有后缀覆盖，网页不会白屏。

D2：`semantic_required` 的来源校验由 `required` 放宽为 `required + conditional`，而不是把首方域名从 `conditional` 提升到 `required`。理由：`conditional` 表达的「按账号或功能条件发射」语义应当保留，而哨兵表达的是「必须存在」，二者职责不同。

D3：改动通过契约层生效，不手工编辑 `main.js` / `shadowrocket.conf` 的生成块。

D4（对应 Q1 提交范围）：以单个提交收口整个 AI 路由变更集，涵盖契约套件（`ai-routing-contract.json`、`ai-routing-contract.schema.json`、`subscription-hub/ai_routing.py`、`scripts/render-ai-routing.py`、`scripts/check-ai-routing.js`、`subscription-hub/tests/`）、三处接入点（`main.js`、`shadowrocket.conf`、`subscription-hub/rules.json`）、其余 `subscription-hub/` 与 `scripts/` 的改动文件，以及 `.comet/config.yaml` 与 `docs/` 产物；排除 `.playwright-cli/`。理由：这些文件构成同一逻辑变更集，且 `main.js`、`shadowrocket.conf`、`rules.json` 中本次改动与既有改动无法按文件切分。

D5（对应 Q2 部署范围）：执行全套部署，包含 SJC 规则镜像同步、`shadowrocket.conf` 以 jsdelivr commit-pinned URL 发布、以及 `main.js` 经 `deploy-substore` 投递到 Sub-Store。理由：只同步规则镜像不会让两端新增的内联规则生效，仍缺 `claudemcpclient.com` 与 `claudemcpcontent.com`。

D6（对应 Q3 Sparkle 同步）：先以默认 dry-run 产出 `_staging\19d8b14dfd4.js.test` 并核对新增两条域名存在，再以 `-Production` 覆盖生产 override，保留 `.bak-<timestamp>` 自动备份。

# 待解决问题

无。Q1、Q2、Q3 均已在决策 D4、D5、D6 中记录结论。

# 验证预期

以本地命令检查为主：`render-ai-routing.py --check`、`rule_mirror.py --check`、`pytest`、`py_compile`、`node --check`，加上私有临时目录的真实 sync 与一次负向拦截验证。生产侧以 `rules/status.json` 的计数和镜像文件内容为准。Sparkle 侧以 override 文件的新旧对比与备份文件存在性为准。全部结论必须来自实际执行结果，不以推断代替。
