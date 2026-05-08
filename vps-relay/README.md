# vps-relay

VPS-LA 链式代理生成器：把 cnqq aggregated-residential 里**本机超时但 VPS 视角能拨通**的家宽节点，自动包装成可被三端（Sparkle / FlClash / Shadowrocket）直接消费的 vmess+ws+TLS 节点。

> 单一信息源 = `chain-registry.tsv`。改这一个文件 → 跑 `bash scripts/deploy-relay.sh` → mihomo-relay / nginx / Sub-Store 全自动同步。

## 文件

| 路径 | 内容 |
|---|---|
| `chain-registry.tsv` | 链式节点白名单（每行 1 个原家宽节点）|
| `generate.py` | 读 TSV + 凭据 → 生成 mihomo config / nginx conf / vmess link bundle |
| `scripts/deploy-relay.sh` | 一行命令部署：scp → docker restart → nginx reload → PATCH Sub-Store |
| `.secrets.local/credentials.env` | **gitignored**：含 relay UUID / WS path / backend path / public host / OpenResty 运行态 |

## 日常维护

```bash
# 1. 加 / 删 / 改链式节点：编辑 chain-registry.tsv
vim vps-relay/chain-registry.tsv

# 2. 跑部署
bash vps-relay/scripts/deploy-relay.sh

# 3. (可选) commit
git add vps-relay/chain-registry.tsv
git commit -m "chain: <说明>"
git push
```

30 秒内三端订阅自动同步。

## 架构

```
chain-registry.tsv  (单一信息源)
        │
        ↓  python3 generate.py
   ┌────┴────────────────┬─────────────────────┐
   │                     │                     │
config.yaml      relay.conf            vmess-bundle.txt
(mihomo-relay    (nginx N location)    (base64 V2Ray URI list)
 N listener)            │                     │
   │                    │                     ↓
   ↓                    ↓              Sub-Store sub vps-chain-residential
 VPS:18443~184xx   openresty :443 反代       │
   │                    │                     ↓
   └────────────────────┴────────────────  merged-airports collection
                                                    │
                                                    ↓
                                      三端客户端订阅看到 N 条链式节点
```

## 节点命名约定

- 输出节点名 = `🏡 链式 <client_display_name> 家宽`
- 含 `家宽` 二字 → 被 main.js 的 `RESIDENTIAL_PATTERN` 命中 → 自动入 `🏡 家宽选择` selector
- 含中文区域（美国 / 法国 / ...）→ 被区域 url-test 组（`🏡 美国家宽` / `🏡 欧洲家宽`）命中
- 加 `链式` 前缀 → 跟直连节点视觉区分

## 凭据管理

`.secrets.local/credentials.env`（gitignored）：

```
RELAY_VM_UUID=<uuid>
RELAY_WS_PATH=/relay-<32-char hex>
BACKEND_PATH=/api/<sub-store backend path>
RELAY_PUBLIC_HOST=<sub-store public host>
OPENRESTY_CONTAINER=<1Panel openresty container name>
OPENRESTY_SITE_DIR=/www/sites/<sub-store public host>/proxy
SSH_TARGET=vps
```

凭据**轮换**：改 .env → 跑 deploy-relay.sh → 客户端订阅 30s 内自动拉新 vmess link。

## 一次性前置（首次部署 / 凭据轮换后）

1. **VPS-LA mihomo-relay 容器**已部署（参 `.trellis/tasks/05-08-vps-residential-chain-feasibility/deploy/mihomo-relay/`）
2. **1Panel openresty 反代** `<RELAY_PUBLIC_HOST>` 已支持 WebSocket
3. **Sub-Store sub `vps-chain-residential`** 存在（local 类型，content 可初始为空）已加进 merged-airports collection 的 subscriptions

如果上述任一未就绪，参 `.trellis/tasks/05-08-vps-residential-chain-feasibility/deploy/USER-MANUAL.md`。

## 故障排查

| 症状 | 原因 | 修复 |
|---|---|---|
| `generate.py` 报 missing secret keys | .secrets.local/credentials.env 不全 | 补齐 4 个必填 key |
| `deploy-relay.sh` 第 2 步 mihomo-relay 容器没起 | yaml 语法错 / proxy 字段引用了不存在的节点 | 查 `ssh vps 'docker logs mihomo-relay'` 看错误，修 chain-registry.tsv |
| 第 3 步 nginx -t 失败 | location 块语法错 | 看 ssh stderr 输出 |
| 第 4 步 Sub-Store PATCH 200 但客户端看不到节点 | sub vps-chain-residential 没加进 merged-airports | Sub-Store 后台编辑 merged-airports collection 勾选 vps-chain-residential |
| 客户端看到节点但选了连不上 | 原家宽节点 cnqq 那侧死了 / mihomo-relay 内部 outbound 拿不到 | mihomo-relay 重启 + 看 docker logs |

## 退役

`chain-registry.tsv` 清空注释行以外所有行 → 跑 deploy-relay.sh → mihomo-relay 0 listener，nginx 0 location，Sub-Store sub content 空。客户端订阅自动收缩。
