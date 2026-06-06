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

Client-facing subscriptions should expose these few roles, not every raw
supplier node.

## One-Time Target Sub-Store Setup

Create these objects manually in the target Sub-Store Web panel:

| Object | Type | Purpose |
|---|---|---|
| `edge-us-upstreams` | Collection | AT&T SS and future US residential upstreams |
| `edge-us-roles` | Local sub | Generated US edge role vmess links |

Add `edge-us-roles` to the normal node collection used by Sparkle / FlClash /
OpenClash and Shadowrocket's ordinary `target=URI` feed. Do not add it to the
Evoxt HY2 Shadowrocket feed.

Keep `edge-us-upstreams` narrow: only put US residential upstreams intended for
this edge in it. The `美国-家宽自动` role uses the whole collection, while the
stable `美国-AT&T家宽` role applies a supplier-specific filter.

The AT&T SS URI belongs only in the `edge-us-upstreams` runtime collection. If
Sub-Store keeps the upstream display name as `微信kuma`, the default
`US-Edge | 美国-AT&T家宽` role already matches it; if the supplier renames the
node later, update only `edge-roles.tsv`, regenerate, and patch `edge-us-roles`.

Do not use `POST /api/subs`. New local subs are created in the Web panel; scripts
only `PATCH /api/sub/<existing>`.

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
bundle into the existing local sub:

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

# From a client or test host
# 1. refresh the final mihomo subscription
# 2. run mihomo.exe -t against the downloaded profile
# 3. select each US-Edge role and compare ipinfo.io exit ASN/ISP
```

Expected behavior:

- `美国-VPS直出` exits as the US VPS ISP.
- `美国-AT&T家宽` exits through the AT&T or supplier residential upstream, not the VPS IP.
- `美国-家宽自动` selects a real residential upstream. Do not add a direct sentinel
  to this `url-test`; direct is treated as zero delay and will always win.

## Boundaries

- This does not migrate the old VPS-LA 26-chain relay.
- This appliance is not a control panel. Hiddify, Marzban, and 3x-ui remain
  optional upstream/node-management systems, not requirements for the US
  subscription control plane.
- This does not change Shadowrocket's three-entry model.
- This directory must never contain real subscription URLs, tokens, UUIDs,
  backend paths, passwords, private key paths, or server IPs.
