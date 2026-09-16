# frontier-chain-skeleton

VPS/Sub-Store 三端订阅中心的公开源码层。当前生产主链是：

```text
机场订阅 + 家宽订阅
  -> Sub-Store merged-airports Collection
  -> shadowrocket-nodes-injector.js 清洗 / 归一化 / 过滤
  -> main.js 生成最终 mihomo YAML
  -> Sparkle(Windows) / FlClash(Android)

Shadowrocket(iOS)
  -> shadowrocket.conf 配置订阅
  -> ios-airports-uri?target=URI 普通机场/家宽节点订阅
  -> ios-evoxt-hy2-shadowrocket?target=ShadowRocket HY2 专用节点订阅
```

核心边界：业务组只认识稳定的 `🏡 家宽选择`，不再引用 Frontier、ScrapeGW、VPS 链式节点、区域家宽组或任何具体供应商节点名。

## Sub-Store 对象命名

Sub-Store 后台只把显示名改成小白可读的分层；内部 `name`、share token、backend path、客户端订阅 URL 都不能改。

| 前缀 | 含义 | 例子 |
|---|---|---|
| `10-原料-普通机场-*` | 普通机场上游，暂不角色化 | `10-原料-普通机场-CCR` |
| `20-原料-家宽-*` | 家宽供应商或 edge 家宽上游 | `20-原料-家宽-美国-AT&T` |
| `30-原料-Evoxt-HY2` | HY2 专用上游 | `30-原料-Evoxt-HY2` |
| `40-稳定角色-*` | 客户端可消费的稳定角色节点 | `40-稳定角色-美国Edge家宽` |
| `80/81/82-输出-*` | 三端最终输出 | `80-输出-Sparkle-FlClash-OpenClash-最终配置` |
| `99-历史禁用-*` | 保留追溯但不进日常输出 | `99-历史禁用-VPS-LA-*` |

家宽 remote 上游默认启用失败隔离：供应商拉取失败时后台验证会提示，但 final Mihomo 和 Shadowrocket 普通节点订阅不应因为单个家宽供应商失效而整体 HTTP 500。

## 当前能力

- Sub-Store 合并普通机场订阅与家宽订阅。
- 过滤伪节点、不可直连提示节点、已知超时家宽节点。
- 统一节点名前缀；具体前缀来自 Sub-Store 上游，不作为业务规则硬依赖。
- mihomo 端新增 `🏡 家宽选择`：`select + include-all + filter` 动态吸纳家宽候选。
- Shadowrocket 端保留双订阅模型，用 `select + policy-regex-filter` 动态列出家宽候选。
- AI / PayPal / Google 等业务组只追加 `🏡 家宽选择`；区域家宽组只在 `🏡 家宽选择` 内部展示。
- 家宽供应商只作为后台原料，客户端菜单稳定为 `🏡 家宽选择` / `🏡 美国家宽` / `🏡 亚太家宽` 等角色。
- Claude / Anthropic 官方域以内联 `DOMAIN-SUFFIX,anthropic.com` 加 `claude.ai` / `claude.com` / `claudeusercontent.com` 走 `AI服务`，不依赖 RULE-SET 镜像是否拉取成功。Vertex 的 `claude.googleapis.com` 也走 `AI服务`。`ANTHROPIC_BASE_URL` 私有中转不得进 Git；用 Sub-Store 参数 `extra_ai_api_hosts` 或 gitignore 的 `extra-ai-hosts.local.js`。

## 敏感信息边界

本仓库不存任何真实订阅 URL、token、后端路径、密码或 VPS 凭据。

敏感值只放在目标 VPS Sub-Store 运行态，或通过部署命令的环境变量临时传入：

```powershell
$env:FRONTIER_RESIDENTIAL_AGGREGATOR_URL = '<NEW_AGGREGATOR_SUBSCRIPTION_URL>'
```

不要把订阅 URL 写进 README、脚本、issue、commit message 或公开 jsdelivr URL。

## 文件职责

| 文件 | 用途 |
|---|---|
| `substore-source-marker.js` | 挂在每个上游 subscription 上，给节点临时打来源前缀 |
| `shadowrocket-nodes-injector.js` | Collection 节点清洗、过滤、归一化；不注入自建链式节点 |
| `main.js` | 生成最终 mihomo profile，新增 `🏡 家宽选择` 和业务组镜像 |
| `shadowrocket.conf` | Shadowrocket 公开配置订阅，保留双订阅模型 |
| `scripts/deploy-substore.ps1` | 把公开脚本发布到 VPS Sub-Store，可新增家宽上游 |
| `scripts/verify-substore.ps1` | 本地语法检查 + 远端只读验收 |
| `scripts/restore-substore-backup.ps1` | 列出或恢复 VPS `sub-store.json` 备份 |

## Sub-Store 维护流程

先做 dry-run：

```powershell
.\scripts\deploy-substore.ps1
```

发布脚本改动到 VPS：

```powershell
.\scripts\deploy-substore.ps1 -Apply `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>
```

首次新增或替换某个家宽聚合订阅时，把 URL 临时放入环境变量：

```powershell
$env:FRONTIER_RESIDENTIAL_AGGREGATOR_URL = '<NEW_AGGREGATOR_SUBSCRIPTION_URL>'
.\scripts\deploy-substore.ps1 -Apply `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>
Remove-Item Env:\FRONTIER_RESIDENTIAL_AGGREGATOR_URL
```

脚本会：

- 备份 VPS 上的 `sub-store.json`。
- 可选新增或更新一个家宽聚合上游订阅。
- 统一后台显示名为 `10/20/30/40/80/99` 分层，并给家宽 remote 上游启用失败隔离。
- 默认保留目标 Sub-Store 里已有的 iOS 普通/HY2 集合组成；如果目标集合不存在，则从源集合推导普通节点池。
- 更新 source marker、Collection 清洗脚本和 mihomo 主脚本。
- 清理旧 `frontier_*` / `scrapegw_*` / `vps_*` Script Operator arguments。
- 重启 `sub-store` 容器。

## 验证

本地和远端只读验证：

```powershell
.\scripts\verify-substore.ps1 `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path>
```

迁移控制面且要求客户端旧 URL 不变时，额外传入旧 URL 里的 backend path 做兼容校验：

```powershell
$env:FRONTIER_EXPECTED_BACKEND_PATH = '<EXISTING_CLIENT_BACKEND_PATH>'
.\scripts\verify-substore.ps1 `
  -SshHost <vps-host> `
  -SshPort <ssh-port> `
  -SshUser root `
  -SshKey <private-key-path> `
  -SubStoreDir /opt/frontier/sub-store `
  -SubStoreDataPath /opt/frontier/sub-store/data/sub-store.json `
  -ContainerName frontier-sub-store `
  -LocalBaseUrl http://127.0.0.1:19093
Remove-Item Env:\FRONTIER_EXPECTED_BACKEND_PATH
```

验收重点：

- `docker logs sub-store --tail 200` 无 `missing` / `error` / `fail` / `exception`。
- 控制面迁移时，Sub-Store 容器 env 里的 backend path 必须匹配客户端正在使用的旧 URL path；脚本只输出长度和是否匹配。
- 源集合和 iOS 普通集合都存在，且包含至少一个上游引用。
- Sub-Store 后台显示名符合 `10/20/30/40/80/99` 分层；旧 VPS-LA 对象不在日常客户端集合里。
- 家宽 remote 上游启用失败隔离；供应商失效不能拖垮最终订阅。
- iOS 普通机场/家宽订阅 `ios-airports-uri?target=URI` 输出原生 URI，且不含 Evoxt。
- iOS HY2 专用订阅 `ios-evoxt-hy2-shadowrocket?target=ShadowRocket` 与普通 URI feed 分离；内部对象名沿用旧 Evoxt 命名，但显示层按 HY2 专用输出理解。
- 输出中没有 `[VPS->家宽]`、`[机场->家宽]`、`Frontier`、`ScrapeGW`。
- 输出中没有伪节点、不可直连提示节点、已知超时家宽节点。
- final mihomo YAML 含 `🏡 家宽选择`，且 profile-check 通过。

## Shadowrocket

Shadowrocket 仍使用双订阅：

1. 配置订阅：SJC 订阅入口中心提供的稳定 Shadowrocket 配置链接。
2. 普通机场/家宽节点订阅：SJC 订阅入口中心提供的普通节点链接，内部反代到 Sub-Store `/download/collection/ios-airports-uri?target=URI`。
3. HY2 专用节点订阅：SJC 订阅入口中心提供的 HY2 节点链接，内部反代到 Sub-Store `/download/collection/ios-evoxt-hy2-shadowrocket?target=ShadowRocket`。

客户端订阅 URL 形态必须区分 File API 和 Collection 下载口：

```text
Sparkle / FlClash / OpenClash:
  https://<SUBSTORE_PUBLIC_HOST><BACKEND_PATH>/api/file/frontier-chain-mihomo?target=ClashMeta

Shadowrocket 普通节点:
  https://<SUBSTORE_PUBLIC_HOST><BACKEND_PATH>/download/collection/ios-airports-uri?target=URI

Shadowrocket HY2 专用节点:
  https://<SUBSTORE_PUBLIC_HOST><BACKEND_PATH>/download/collection/ios-evoxt-hy2-shadowrocket?target=ShadowRocket
```

常见误区：

- 少了 backend path 前缀会返回 `404`。
- 使用旧 `/file/<name>?token=...` 形态会命中旧兼容或旧容器，可能返回 `404/500`。
- 把 Collection 写成 `/api/collection/<name>?target=URI` 会拿到后台 JSON 包装，不是客户端可直接导入的节点订阅。
- FlClash / Sparkle 不能消费 `ios-airports-uri`；它们只消费 `frontier-chain-mihomo` final Mihomo YAML。

当前 Sub-Store 的 `target=ShadowRocket` 会输出 `proxies:` YAML。Shadowrocket 对普通机场节点解析这种 YAML 时会出现通用蓝色图标、测速异常等兼容问题；但 HY2 节点在 `target=URI` 下反而会出现图标/测速异常。因此 iPhone 节点订阅拆成两条：普通机场/家宽走 `target=URI`，HY2 专用 feed 保持 `target=ShadowRocket`。`ios-evoxt-hy2-shadowrocket` 是历史内部名，不代表这个输出只能放 Evoxt。

`shadowrocket.conf` 中的 `🏡 家宽选择` 是手动 selector。用户在这个 selector 内选择具体家宽节点；AI / PayPal 等业务组保持选中 `🏡 家宽选择` 即可。

不要把 jsDelivr 的 `@main/shadowrocket.conf` branch ref 作为 iPhone 长期配置订阅。jsDelivr 对 branch ref 有缓存，Shadowrocket 也可能保留旧配置；现在给小白使用的正式入口是 SJC 订阅入口中心里的稳定 Shadowrocket 配置链接。

```text
https://<SUBSCRIPTION_HUB_PUBLIC_HOST>/<OPAQUE_SHADOWROCKET_CONFIG_PATH>
```

同理，`shadowrocket.conf` 内部不要再引用本仓库的 `@main` 资源。少量自有规则（例如 AI 扩展域名）直接内联在配置里；第三方规则通过 SJC `/rules/*` 镜像定时刷新；Sub-Store 节点清洗脚本用 commit-pinned URL 下载后以内联 Script Operator 形式保存到 Sub-Store。

## SSH 分流维护

Mihomo 不能靠 sniffer 识别任意端口上的 SSH 协议；SSH 分流由 `main.js` 顶部的 `SSH_ROUTING` 集中登记表生成，并统一开启 `find-process-mode: always` 让 `PROCESS-NAME` / `PROCESS-PATH-WILDCARD` 生效。新增 SSH 客户端或非标准 SSH 端口时，只改这个登记表，不要在 `rules` 里散落手写规则。

- 新 SSH 服务器使用非标准端口：把端口加到 `SSH_ROUTING.ports`。
- 新客户端在 Sparkle/Mihomo 连接详情里显示为明确 SSH 客户端进程：把进程名加到 `SSH_ROUTING.processNames`。
- FinalShell 等 Java 包装客户端如果显示为 `java.exe`：先复制连接详情里的进程路径，再把安装目录精确加到 `SSH_ROUTING.processPathWildcards`；不要添加裸 `java.exe`。
- VS Code Remote SSH 通常复用 Windows OpenSSH 的 `ssh.exe`，一般不需要额外添加 `Code.exe`。
- 不要用目标 IP 全匹配来判定 SSH；同一 VPS IP 可能同时承载代理节点、HTTPS 或其他业务端口。

每次修改后运行 verifier，确认 final Mihomo 中 `SSH` 组存在，`find-process-mode` 为 `always`，必需端口/进程规则存在，且所有 SSH 规则都在规则表前部和 `MATCH` 前。

## 已退役内容

- 不再生成 `🏠 [VPS->家宽] Frontier`。
- 不再生成 `🏠 [机场->家宽] Frontier` / `ScrapeGW`。
- 不再维护 Sparkle 本地凭据拼接流程。
- `creds.local.example.js` 只保留为空占位，避免旧文档诱导继续创建本地敏感凭据。

MIT
