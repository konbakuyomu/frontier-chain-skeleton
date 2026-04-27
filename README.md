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
4. 订阅给 iPhone 时，URL fragment 注入凭据：

```
https://your-substore-domain/<api-prefix>/api/file/<filename>?target=mihomo#scrapegw_host=<host>&scrapegw_port=<port>&scrapegw_user=<user>&scrapegw_pass=<pass>&frontier_server=<host>&frontier_password=<pass>&vps_server=<host>&vps_password=<pass>
```

> Fragment（`#` 之后部分）不会发送给服务器，由客户端本地解析后传给脚本的 `$arguments`，因此凭据**不会**写入 Sub-Store 服务端日志或数据库。

### iPhone Shadowrocket

直接添加上一步的 Sub-Store mihomoProfile URL 为订阅源（含 `#args` fragment）。要求 Shadowrocket ≥ 2.2.x（2024+ 版本原生支持完整 Clash YAML）。

## 安全注意

- 本仓库**不包含任何凭据**。所有密码 / token 都通过运行时参数注入。
- 任何形式的凭据 commit 到本仓库都是事故 —— 立即 rotate 全部凭据 + force-push 重写历史 + 通知所有引用方。
- Sub-Store 端 `$arguments` 走 URL fragment（不入服务端）；Sparkle 端凭据放本地 patch 脚本（不进 git）。

## 许可

MIT
