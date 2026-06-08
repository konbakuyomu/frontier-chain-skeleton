# frontier-edge

No-panel edge appliance for the Frontier subscription stack.

The daily control point is the target Sub-Store for the deployment. In the US
migration, that means the US VPS-local Sub-Store owns publication; the Malaysia
Sub-Store is no longer the central control plane after cutover. This directory
only renders a small Docker Compose appliance for a VPS:

```text
edge-roles.tsv
  -> generate.py
  -> .secrets.local/out/mihomo/config.yaml
  -> .secrets.local/out/Caddyfile or openresty/<host>.conf
  -> .secrets.local/out/compose.yaml
  -> .secrets.local/out/vmess-bundle.txt
  -> target Sub-Store local sub edge-us-roles
edge-hy2-roles.tsv
  -> generate.py
  -> .secrets.local/out/mihomo/config.yaml HY2 UDP listeners
  -> .secrets.local/out/hy2-bundle.txt
  -> target Sub-Store local sub edge-us-hy2-roles
```

## Why This Exists

Hiddify, Marzban, and 3x-ui are node/user panels. They are useful, but adding
one on every VPS creates another daily management surface. This appliance keeps
the US VPS boring: either Caddy owns TLS on an empty/lab VPS, or an existing
OpenResty ingress terminates TLS and proxies to mihomo on localhost. Sub-Store
remains the only place where upstream residential suppliers are managed.

## Public Roles

`edge-roles.tsv` is the single editable source:

| role | behavior |
|---|---|
| `US-Edge | 美国-VPS直出` | Exit directly from the US VPS |
| `US-Edge | 美国-家宽自动` | url-test matching US residential upstreams |
| `US-Edge | 美国-AT&T家宽` | stable role backed by matching AT&T upstreams |
| `US-Edge | 美国-VPS直出-HY2` | default low-latency HY2/UDP role exiting directly from the US VPS |
| `US-Edge | 美国-VPS直出-HY2-带宽` | conservative bandwidth-test HY2/UDP canary exiting directly from the US VPS |
| `US-Edge | 美国-AT&T家宽-HY2` | parked HY2/UDP role for the AT&T upstream until its whitelist is ready |

Client-facing subscriptions should expose these few roles, not every raw
supplier node.

HY2 is not WebSocket traffic. It is served by Mihomo `hysteria2` UDP listeners
and does not go through the OpenResty `location` blocks used by VMess. Existing
`443/tcp` OpenResty/Caddy service remains unchanged; the default direct HY2 role
uses `443/udp`, and the bandwidth canary uses the fixed high UDP port declared
in `edge-hy2-roles.tsv`. Caddy must never publish `443/udp`.

`edge-hy2-roles.tsv` has a `profile` column:

| profile | behavior |
|---|---|
| `latency` | daily/default HY2 role; no fixed `up`/`down` or QUIC window overrides |
| `bandwidth` | conservative Brutal canary; client-side target `up=100 Mbps`, `down=500 Mbps`, with larger QUIC receive windows |
| `parked` | documented role only; no listener, compose port, or client bundle output |

The bandwidth profile writes server listener bandwidth in server-side direction
(`up=500 Mbps`, `down=100 Mbps`) and client bundle bandwidth in client-side
direction (`up=100 Mbps`, `down=500 Mbps`). Do not raise this to a nominal
`1 Gbps` without A/B evidence from Sparkle and server drop/CPU counters.

## One-Time Target Sub-Store Setup

Create these objects manually in the target Sub-Store Web panel:

| Object | Type | Purpose |
|---|---|---|
| `edge-us-upstreams` | Collection | AT&T SS and future US residential upstreams |
| `edge-us-roles` | Local sub | Generated US edge role vmess links |
| `edge-us-hy2-roles` | Local sub | Generated US edge HY2 role YAML |

Add `edge-us-roles` to the normal node collection used by Sparkle / FlClash /
OpenClash and Shadowrocket's ordinary `target=URI` feed. Add
`edge-us-hy2-roles` to the normal node collection and to the Shadowrocket HY2
feed collection. Do not add `edge-us-hy2-roles` to Shadowrocket's ordinary URI
feed.

Keep `edge-us-upstreams` narrow: only put US residential upstreams intended for
this edge in it. The `美国-家宽自动` role uses the whole collection, while the
stable `美国-AT&T家宽` role applies a supplier-specific filter.

The AT&T SS URI belongs only in the `edge-us-upstreams` runtime collection. If
Sub-Store keeps the upstream display name as `微信kuma`, the default
`US-Edge | 美国-AT&T家宽` role already matches it; if the supplier renames the
node later, update only `edge-roles.tsv`, regenerate, and patch `edge-us-roles`.
The AT&T HY2 role stays `parked` in `edge-hy2-roles.tsv` while the upstream
whitelist is unavailable; do not expose it to clients until a separate
data-plane test passes.

Do not use `POST /api/subs`. New local subs are created in the Web panel; scripts
only `PATCH /api/sub/<existing>`.

The Shadowrocket HY2 collection keeps the historical internal name
`ios-evoxt-hy2-shadowrocket`, but its meaning is HY2-only Shadowrocket feed. It
may contain Evoxt, MINE, and US Edge HY2 nodes after validation.

## Local Render

Copy the example env and replace every placeholder:

```bash
mkdir -p frontier-edge/.secrets.local
cp frontier-edge/examples/edge-us.env.example frontier-edge/.secrets.local/edge-us.env
python3 frontier-edge/generate.py --check
python3 frontier-edge/generate.py
```

Generated artifacts stay under `frontier-edge/.secrets.local/out/`, which is
gitignored.

Use repeated `--env-file` when keeping shared secrets in a base env and
US production ingress settings in an override. Later files override earlier
ones:

```bash
python3 frontier-edge/generate.py --check \
  --env-file frontier-edge/.secrets.local/edge-us.env \
  --env-file frontier-edge/.secrets.local/edge-us-openresty.override.env
python3 frontier-edge/generate.py \
  --env-file frontier-edge/.secrets.local/edge-us.env \
  --env-file frontier-edge/.secrets.local/edge-us-openresty.override.env
```

To render HY2 candidates, set these only in private env files:

```env
EDGE_ENABLE_HY2=1
EDGE_HY2_PASSWORD=<HY2_PASSWORD>
EDGE_HY2_ROLE_SUB=edge-us-hy2-roles
```

Generated `hy2-bundle.txt` is a ClashMeta/Mihomo YAML snippet for Sub-Store
local sub content. It is private generated output and must not be committed.

## Same-VPS Lab Mode

For the Malaysia Evoxt lab VPS, Sub-Store can stay on the same host while Caddy
replaces the old public ingress. In that mode:

```env
SSH_TARGET=evoxt-my
SUBSTORE_SSH_TARGET=evoxt-my
EDGE_ENABLE_SUBSTORE_PROXY=1
SUBSTORE_DOCKER_NETWORK=sub-store_sub-store_default
SUBSTORE_INTERNAL_UPSTREAM=sub-store:3001
EDGE_ALLOW_REPLACE_INGRESS=1
EDGE_STOP_SERVICES=hiddify-haproxy
```

This is intentionally opt-in. The deployer validates the named service during
preflight, uploads and validates generated artifacts first, then stops the
listed ingress service immediately before `docker compose up -d`. If compose
start fails, it tries to restart the stopped ingress service.

If old Sub-Store collections still reference a Hiddify subscription URL on the
same VPS, replacing `hiddify-haproxy` means that public URL is no longer served
by Hiddify. For a lab only, add an explicit compatibility host:

```env
EDGE_ENABLE_HOST_GATEWAY=1
EDGE_HOST_GATEWAY_IP=host-gateway
EDGE_LEGACY_HTTP_HOST=hiddify.example.com
EDGE_LEGACY_HTTP_UPSTREAM=host.docker.internal:9000
```

That keeps old HTTP subscription downloads reachable through Caddy while the
edge appliance owns `80/443`. It is not a general Hiddify proxy replacement and
should not be used as the final production architecture.

If the host-side backend only listens on `127.0.0.1`, first expose a lab-only
bridge on the `frontier-edge-net` gateway address, then set
`EDGE_HOST_GATEWAY_IP` to that gateway. Do not expose that bridge on `0.0.0.0`.

## US Existing OpenResty Mode

Use this mode when the target VPS already has 1Panel/OpenResty on public
`80/443`. The appliance does not run Caddy and does not stop existing services.
OpenResty reverse proxies each generated WebSocket role path to mihomo on
localhost high ports.

```env
EDGE_INGRESS_MODE=openresty
EDGE_PUBLIC_HOST=edge-us.example.com
EDGE_LISTENER_BASE_PORT=19443
EDGE_CONTROLLER_PORT=19092
EDGE_OPENRESTY_CONTAINER=1Panel-openresty-kOZu
EDGE_OPENRESTY_CONF_DIR=/path/to/openresty/conf.d
EDGE_OPENRESTY_CONF_NAME=edge-us.example.com.conf
EDGE_OPENRESTY_CERTIFICATE=/path/to/wildcard/fullchain.pem
EDGE_OPENRESTY_CERTIFICATE_KEY=/path/to/wildcard/privkey.pem
EDGE_ALLOW_REPLACE_INGRESS=0
```

Before patching the target Sub-Store, the deployer requires `EDGE_PUBLIC_HOST` to resolve
to the target VPS public IP from that VPS. If DNS is not ready, deploy without
`--patch-substore` and verify internally first.

When `EDGE_ENABLE_HY2=1`, the deployer also checks all generated UDP ports. If
`443/udp` or the fixed high UDP port is already busy and not owned by the
existing `frontier-edge-mihomo` container, deployment stops. Identify the owner
before changing any service.

## Deploy

Before the first SSH operation, handle any `REMOTE HOST IDENTIFICATION HAS
CHANGED` warning from the VPS provider console. Do not bypass it with relaxed
SSH flags.

In the default Caddy mode, deploy refuses to continue if remote `80` or `443`
already have listeners and they are not owned by an existing
`frontier-edge-caddy` container. For a VPS where 1Panel/OpenResty or another
production ingress already owns `80/443`, use existing OpenResty mode; do not
let Caddy replace the current public entry point.

```bash
bash frontier-edge/scripts/deploy-edge.sh
```

For existing OpenResty mode:

```bash
bash frontier-edge/scripts/deploy-edge.sh \
  --env-file frontier-edge/.secrets.local/edge-us.env \
  --env-file frontier-edge/.secrets.local/edge-us-openresty.override.env
```

After the target Web panel has `edge-us-roles` and `edge-us-upstreams`, patch the role
bundle into the existing local sub. If HY2 is enabled and `edge-us-hy2-roles`
exists, the same command patches both local subs:

```bash
bash frontier-edge/scripts/deploy-edge.sh \
  --env-file frontier-edge/.secrets.local/edge-us.env \
  --env-file frontier-edge/.secrets.local/edge-us-openresty.override.env \
  --patch-substore
```

Or patch only:

```bash
bash frontier-edge/scripts/patch-substore.sh \
  --env-file frontier-edge/.secrets.local/edge-us.env \
  --env-file frontier-edge/.secrets.local/edge-us-openresty.override.env
```

## Validation

Minimum checks after deploy:

```bash
# On the US VPS
cd /opt/frontier/frontier-edge
docker compose -f compose.yaml config
docker compose -f compose.yaml ps
docker logs frontier-edge-mihomo --tail=80
docker exec 1Panel-openresty-kOZu openresty -t
ss -H -tulpen | grep -E ':(443|30443)\b'

# From a client or test host
# 1. refresh the final mihomo subscription
# 2. run mihomo.exe -t against the downloaded profile
# 3. select each US-Edge role and compare ipinfo.io exit ASN/ISP
```

Expected behavior:

- `美国-VPS直出` exits as the US VPS ISP.
- `美国-AT&T家宽` exits through the AT&T or supplier residential upstream, not the VPS IP.
- `美国-VPS直出-HY2` uses `443/udp` and remains the default low-latency HY2 role.
- `美国-VPS直出-HY2-带宽` uses the high UDP port and is tested only for throughput.
- `美国-AT&T家宽-HY2` does not appear while its profile is `parked`.
- `美国-家宽自动` selects a real residential upstream. Do not add a direct sentinel
  to this `url-test`; direct is treated as zero delay and will always win.
- `ios-airports-uri?target=URI` must not include `US-Edge | *-HY2`.
- `ios-evoxt-hy2-shadowrocket?target=ShadowRocket` must contain only
  `hysteria2` nodes when non-empty.

## Boundaries

- This does not migrate the old VPS-LA 26-chain relay.
- This appliance is not a control panel. Hiddify, Marzban, and 3x-ui remain
  optional upstream/node-management systems, not requirements for the US
  subscription control plane.
- This does not change Shadowrocket's three-entry model.
- This directory must never contain real subscription URLs, tokens, UUIDs,
  backend paths, passwords, private key paths, or server IPs.
