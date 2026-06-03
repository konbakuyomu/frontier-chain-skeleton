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
  -> merged-airports?target=URI 节点订阅
```

核心边界：业务组只认识稳定的 `🏡 家宽选择`，不再引用 Frontier、ScrapeGW、VPS 链式节点、区域家宽组或任何具体供应商节点名。

## 当前能力

- Sub-Store 合并普通机场订阅与家宽订阅。
- 过滤伪节点、不可直连提示节点、已知超时家宽节点。
- 统一节点名前缀，例如 `CCR | ...`、`KUMA | ...`、`AGG | ...`。
- mihomo 端新增 `🏡 家宽选择`：`select + include-all + filter` 动态吸纳家宽候选。
- Shadowrocket 端保留双订阅模型，用 `select + policy-regex-filter` 动态列出家宽候选。
- AI / PayPal / Google 等业务组只追加 `🏡 家宽选择`；区域家宽组只在 `🏡 家宽选择` 内部展示。

## 敏感信息边界

本仓库不存任何真实订阅 URL、token、后端路径、密码或 VPS 凭据。

敏感值只放在 VPS Sub-Store 运行态，或通过部署命令的环境变量临时传入：

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

首次新增或替换家宽聚合订阅时，把 URL 临时放入环境变量：

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
- 新增或更新 `aggregated-residential` 上游订阅。
- 把它加入 `merged-airports`，同时保留 `ccrui` / `kuma`。
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

验收重点：

- `docker logs sub-store --tail 200` 无 `missing` / `error` / `fail` / `exception`。
- `merged-airports` 同时包含 `ccrui`、`kuma`、`aggregated-residential`。
- `?target=ClashMeta` 与 `?target=URI` 的节点 name 列表一致。
- 输出中没有 `[VPS->家宽]`、`[机场->家宽]`、`Frontier`、`ScrapeGW`。
- 输出中没有伪节点、不可直连提示节点、已知超时家宽节点。
- final mihomo YAML 含 `🏡 家宽选择`，且 profile-check 通过。

## Shadowrocket

Shadowrocket 仍使用双订阅：

1. 配置订阅：`shadowrocket.conf` 的 commit-pinned jsDelivr URL。
2. 节点订阅：VPS/Sub-Store 的 `merged-airports?target=URI`。

当前 Sub-Store 的 `target=ShadowRocket` 会输出 `proxies:` YAML。Shadowrocket 虽然可能部分识别，但会出现通用蓝色图标、测速异常等兼容问题。iPhone 正式节点订阅使用 `target=URI`，它输出一行一个原生节点 URI，节点名集合与 `ClashMeta` 一致。

`shadowrocket.conf` 中的 `🏡 家宽选择` 是手动 selector。用户在这个 selector 内选择具体家宽节点；AI / PayPal 等业务组保持选中 `🏡 家宽选择` 即可。

不要把 jsDelivr 的 `@main/shadowrocket.conf` branch ref 作为 iPhone 长期配置订阅。jsDelivr 对 branch ref 有缓存，Shadowrocket 也可能保留旧配置；正式发布后使用：

```text
https://cdn.jsdelivr.net/gh/konbakuyomu/frontier-chain-skeleton@<COMMIT>/shadowrocket.conf
```

同理，`shadowrocket.conf` 内部不要再引用本仓库的 `@main` 资源。少量自有规则（例如 AI 扩展域名）直接内联在配置里；Sub-Store 节点清洗脚本用 commit-pinned URL 下载后以内联 Script Operator 形式保存到 Sub-Store。

## 已退役内容

- 不再生成 `🏠 [VPS->家宽] Frontier`。
- 不再生成 `🏠 [机场->家宽] Frontier` / `ScrapeGW`。
- 不再维护 Sparkle 本地凭据拼接流程。
- `creds.local.example.js` 只保留为空占位，避免旧文档诱导继续创建本地敏感凭据。

MIT
