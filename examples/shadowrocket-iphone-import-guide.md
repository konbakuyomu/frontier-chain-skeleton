# iPhone Shadowrocket 双订阅导入指南

> 适用版本: Shadowrocket ≥ 2.2.x
> 架构: 配置订阅 (无凭据, 公开 commit-pinned jsDelivr) + 节点订阅 (凭据只在 Sub-Store 运行态中)
> 上次更新: 2026-06-03

---

## 0. 前置条件

| 项 | 要求 |
|---|---|
| Shadowrocket 版本 | ≥ 2.2.x (App Store 安装) |
| Sub-Store 后端 | 已部署, 反代 HTTPS 可访问 |
| 机场订阅 | 至少 1 个 (推荐 2 个, 配 Sub-Store Collection 合并) |
| 公开仓库 | `konbakuyomu/frontier-chain-skeleton` 已提交并拿到本次发布 commit |

---

## 1. 检查 jsDelivr URL 通断

任意 PC 终端跑：

```bash
curl -sI https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/shadowrocket.conf | head -1
curl -sI https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/ai-extensions.list | head -1
curl -sI https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/shadowrocket-nodes-injector.js | head -1
```

三条均应返回 `HTTP/2 200`。不要用 `@main/shadowrocket.conf` 做正式配置订阅；branch ref 会被 CDN/客户端缓存。

---

## 2. Sub-Store 后台准备节点订阅

### 2.1 添加机场订阅

Sub-Store 后台 → Subscriptions → + → 粘贴机场原始订阅 URL。重复添加多个机场。

### 2.2 创建组合 Collection

Sub-Store 后台 → Collections → + → 名称 `merged-airports` → 勾选上一步添加的全部机场订阅。

### 2.3 挂 Script Operator (节点归一化)

Collections 详情页 → Process → Add Operator → Script Operator：

1. 从 commit-pinned URL 获取脚本正文：

```
https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/shadowrocket-nodes-injector.js
```

2. 使用 **normal / inline** 模式粘贴脚本正文，不使用 link-mode 直接引用 URL。
3. `type` 选 `节点处理脚本` (proxies array)。
4. `arguments` 只放非敏感策略参数；供应商订阅 URL 留在 Sub-Store 上游运行态。
5. Save。

> Sub-Store v2.22.8 实测：Script Operator `mode: link` + 纯 URL 不会稳定传递 UI `arguments`。当前推荐 inline 脚本正文。

### 2.4 拼节点订阅 URL

直接使用 Collection 下载 URL：

```
https://<sub-store-host>/<api-prefix>/download/collection/merged-airports?target=ShadowRocket
```

不要在 iPhone 订阅 URL 后追加凭据 fragment。

### 2.5 后台预览验证

Sub-Store 后台 → Collection `merged-airports` → Preview。应能看到机场节点、家宽候选和 Evoxt HY2 节点。

如节点缺失：

- 确认 Script Operator 是否正确加在 Collection 的 Process 流程里
- 确认 Script Operator 是 inline/normal 模式，不是 link-mode 纯 URL
- 确认脚本正文来自可访问的 commit-pinned jsdelivr URL

---

## 3. iPhone Shadowrocket 端导入

### 3.1 导入配置订阅 (无凭据)

打开 Shadowrocket → 配置 (Config) → 右上角 + → 类型选 Subscription → URL：

```
https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/shadowrocket.conf
```

下载 → 设为使用中。

### 3.2 导入节点订阅

Shadowrocket → 首页 (Home) → 订阅 (Subscribe) → + → 粘贴第 2.5 步的节点订阅 URL（不含凭据 fragment）→ Update。

### 3.3 确认节点列表

回到首页 → 节点列表应同时含：

- 机场节点 (含 🇭🇰 / 🇯🇵 / 🇺🇸 各国家)
- Evoxt HY2 节点应归入 `马来西亚节点`
- 家宽节点名里保留 `家宽` / `住宅` / `Residential` 等关键词

如未出现 Evoxt 或家宽节点：检查 Sub-Store Collection 是否包含对应上游，以及 Script Operator 是否为 inline 模式。

### 3.4 确认策略组配置

Shadowrocket → 首页 → 配置使用中 → 应能看到自动建立的策略组：

```
🚀 节点选择
⚡ 自动选择
香港节点 / 台湾节点 / 日本节点 / 美国节点 / 马来西亚节点 / ...
🏡 家宽选择          <- 自动捕获家宽候选
🤖 AI 服务          <- 默认指向 🏡 家宽选择
🔍 谷歌服务 / 📹 油管视频 / 🛑 广告拦截 / Ⓜ️ 微软服务 / 🍏 苹果服务 / 📲 电报消息 / 🐱 代码托管 / 🏠 私有网络 / 🔒 国内服务 / 🌍 非中国 / 🐟 漏网之鱼
```

---

## 4. 验证清单

| 测试 | 预期 | 失败排查 |
|---|---|---|
| Safari 打开 https://ip.sb，临时切到 `马来西亚节点` 中的 Evoxt HY2 | 显示 Evoxt 出口 | 节点未导入 / Hiddify 上游异常 |
| Safari 打开 https://claude.ai，看 Shadowrocket 流量页 | `claude.ai` 命中 `🤖 AI 服务` 组 → 出口家宽 | LingJingMaster AI.list URL 失效 (查 4.1) |
| Safari 打开 https://chatgpt.com | 同上 (走 AI 组 → 家宽) | 同上 |
| Safari 打开 https://gemini.google.com | 走 `🤖 AI 服务` 组 (注意: **不是** `🔍 谷歌服务`) | `ai-extensions.list` 排序未在 Google.list 之前 (查 4.2) |
| Codex CLI: `cloudaicompanion.googleapis.com` / `cloudcode-pa.googleapis.com` | 走 `🤖 AI 服务` 组 → 家宽 | `ai-extensions.list` 缺 cloudcode-pa / cloudaicompanion 域 |
| Safari 打开 https://baidu.com | 走 `DIRECT` (无延迟) | China.list URL 失效 |
| Safari 打开 https://browserleaks.com/dns | 不见 doh.pub / alidns.com / 国内 ISP DNS | 配置 `dns-server` 字段被覆盖 |

### 4.1 LingJingMaster RULE-SET URL 通断

```bash
curl -sI https://raw.githubusercontent.com/LingJingMaster/Shadowrocket-Rules/refs/heads/main/AI.list | head -1
curl -sI https://raw.githubusercontent.com/LingJingMaster/Shadowrocket-Rules/refs/heads/main/Google.list | head -1
```

GitHub raw 偶发被墙时，临时把 URL 改 `https://cdn.jsdelivr.net/gh/LingJingMaster/Shadowrocket-Rules@main/AI.list` 形式（jsdelivr 反代）。

### 4.2 ai-extensions.list 排序检查

打开 `shadowrocket.conf` → `[Rule]` 段。第 1 条 RULE-SET **必须**是 `ai-extensions.list`，第 4 条才是 LingJingMaster `Google.list`。如顺序反了，Gemini 会先命中 Google 组（走日本节点）而**不是** AI 组（走家宽）。

---

## 5. 常见问题

### Q1：刷新配置订阅后，节点列表变了吗

A：不会消失。配置订阅和节点订阅是**独立**的，刷新配置只更新 [General] / [Proxy Group] / [Rule]，不动 [Proxy] 段（节点列表来自节点订阅）。

如真消失，检查节点订阅是否还在 Shadowrocket → 首页 → 订阅列表。

### Q2：AI 没走家宽，走了机场节点

A：

1. 检查 Shadowrocket → 首页 → 配置使用中 → 策略组 → `🤖 AI 服务` → 当前选中应是 `🏡 家宽选择`
2. 如 `🏡 家宽选择` 里没有家宽节点 → 节点订阅没有拉到家宽候选
3. 如手动切到机场节点 → 长按 `🤖 AI 服务` 重置回 `🏡 家宽选择`

### Q4：iPhone 流量页显示大量 FINAL 兜底

A：参考阶段 5.4 验证。FINAL 兜底比例 > 5% 通常是：

- China.list / Global.list 拉取失败 → 走 4.1 检查 URL
- 自定义域名（小众 SaaS）未被任何 RULE-SET 命中 → 加 [Host] 段或 [Rule] 段定向

### Q5：浏览器报"DNS 泄露"

A：配置中 `dns-server = https://doh.pub/dns-query,...` 默认走国内 DoH，符合"国内域名国内解析、海外域名走代理客户端解析"的设计。如要严格防泄露：

- 改为 `dns-server = https://1.1.1.1/dns-query,https://8.8.8.8/dns-query`
- 但这样国内域名解析会变慢（CDN 调度失准）

---

## 6. 双订阅刷新策略建议

| 订阅类型 | 推荐自动刷新间隔 | 触发场景 |
|---|---|---|
| 配置订阅 (shadowrocket.conf) | 手动更新 | 只有公开配置文件变更后，换成新的 commit-pinned URL |
| 节点订阅 (Sub-Store) | 每天 1 次 | 跟机场流量重置 / 节点上下线 |

Shadowrocket → 首页 → 订阅 → 编辑 → 自动更新间隔。

---

## 附录: URL 速查

| 用途 | URL |
|---|---|
| 配置订阅 | `https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/shadowrocket.conf` |
| AI 扩展规则 | `https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@main/ai-extensions.list` |
| 节点注入脚本 | `https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@main/shadowrocket-nodes-injector.js` |
| 上游 LingJingMaster | https://github.com/LingJingMaster/Shadowrocket-Rules |
| 上游 blackmatrix7 | https://github.com/blackmatrix7/ios_rule_script |
