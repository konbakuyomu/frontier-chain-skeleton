# shared-hy2

Small no-panel Hysteria2 sharing appliance for at most three San Jose users.

This module intentionally stays separate from `frontier-edge`:

- no reuse of the private/self-use HY2 password;
- no `443/udp` publication;
- no Sub-Store collection mutation;
- no public Git storage of live passwords, API secrets, domains, or links.

## Shape

```text
shared-users.tsv
  -> generate.py
  -> .secrets.local/out/compose.yaml
  -> .secrets.local/out/config/u01/hysteria.yaml
  -> .secrets.local/out/links/u01.{hysteria-client.yaml,mihomo.yaml,uri.txt}
  -> /opt/shared-hy2 on the VPS
```

Each user gets one container, one UDP port, one Stats API port, one password,
and one generated client file.

| user | UDP | Stats API |
|---|---:|---:|
| `u01` | `33443/udp` | `127.0.0.1:33501` |
| `u02` | `33444/udp` | `127.0.0.1:33502` |
| `u03` | `33445/udp` | `127.0.0.1:33503` |

## Local setup

```bash
mkdir -p shared-hy2/.secrets.local
cp shared-hy2/examples/shared-hy2.env.example shared-hy2/.secrets.local/shared-hy2.env
python3 shared-hy2/generate.py --init-secrets
python3 shared-hy2/generate.py --check
python3 shared-hy2/generate.py
```

Edit the private env if needed. Do not commit anything under
`shared-hy2/.secrets.local`.

## Deploy

When running from WSL/Git Bash on this Windows machine, prefer the Windows SSH
client so the existing `sjc-snap` alias is used:

```bash
SSH_BIN=ssh.exe bash shared-hy2/scripts/deploy-shared-hy2.sh --canary u01
```

```bash
bash shared-hy2/scripts/deploy-shared-hy2.sh --canary u01
```

After canary validation:

```bash
bash shared-hy2/scripts/deploy-shared-hy2.sh
```

The deploy script uploads generated artifacts to `/opt/shared-hy2`, installs
management commands, and runs `docker compose up -d`.

If a password is rotated directly on the VPS, pull the private env back before
generating local links again:

```powershell
scp sjc-snap:/opt/shared-hy2/secrets/shared-hy2.env .\shared-hy2\.secrets.local\shared-hy2.env
python .\shared-hy2\generate.py
```

## Daily management

Run on the VPS:

```bash
shared-hy2-report
shared-hy2-report u02
shared-hy2-disable u02
shared-hy2-enable u02
shared-hy2-rotate u02
shared-hy2-streams u02
shared-hy2-monitor --daily
```

The report reads only minimum accounting data: user id, traffic counters,
online device count, and warning flags. It does not persist destination domains
or stream target addresses.

`shared-hy2-streams <user>` is an on-demand abuse triage command. It reads the
current Stats API stream dump and prints active targets, but it does not write
those targets into the normal accounting ledger.

## Runtime validation

Run on the VPS:

```bash
cd /opt/shared-hy2 && docker compose config -q
shared-hy2-report
shared-hy2-monitor
shared-hy2-disable u02
shared-hy2-enable u02
shared-hy2-rotate u03
shared-hy2-upgrade-all u03
```

The private client files live in:

```text
shared-hy2/.secrets.local/out/links/<user>/
/opt/shared-hy2/links/<user>/
```

Do not paste those files into Git, Obsidian, or chat.

## Auto-upgrade

The generated compose includes:

```yaml
labels:
  - diun.enable=true
  - autoupgrade.enable=true
```

Use `shared-hy2-upgrade-all` for HY2-specific upgrades. It upgrades users
serially and validates container state, localhost Stats API, and UDP listener
after each user. If one user fails, it rolls back that user and stops.
