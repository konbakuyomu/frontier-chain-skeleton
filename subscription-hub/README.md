# subscription-hub

Private beginner-friendly subscription entry hub for the SJC Sub-Store stack.

The hub does not create nodes or change Sub-Store collections. It only exposes
stable, human-friendly entry points for the existing production outputs:

- Sparkle / FlClash / OpenClash final Mihomo profile
- Shadowrocket config
- Shadowrocket ordinary node feed
- Shadowrocket HY2 node feed
- Third-party rule mirrors for Shadowrocket and Mihomo clients

Live hostnames, paths, backend paths, tokens, and upstream URLs must stay in a
root-only runtime env file on SJC. Do not commit real values.

## Local Check

The renderer uses the local Python package `segno` to generate QR SVG files.
Install it on the deploy workstation only:

```powershell
python -m pip install segno
```

```powershell
python .\subscription-hub\render.py --check `
  --env-file .\subscription-hub\examples\hub.runtime.env.example

python .\subscription-hub\rule_mirror.py --check `
  --registry .\subscription-hub\rules.json
```

The live host runs `subscription-rule-mirror.timer` every 6 hours. The mirror
files are public client resources under `/rules/*`; the status page only records
rule ids, source hosts, byte counts, and hashes.

## Deploy Shape

```powershell
.\subscription-hub\deploy.ps1 `
  -SshHost <VPS_PUBLIC_IP> `
  -SshPort <SSH_PORT> `
  -SshUser root `
  -SshKey <PRIVATE_KEY_PATH>
```

Default mode is dry-run. Add `-Apply` only after the plan looks correct.

To disable the old path-level managed block without deleting runtime files:

```powershell
.\subscription-hub\deploy.ps1 -Disable -Apply `
  -SshHost <VPS_PUBLIC_IP> `
  -SshPort <SSH_PORT> `
  -SshUser root `
  -SshKey <PRIVATE_KEY_PATH>
```

The fixed Caddy mode installs a standalone `links.*` site block instead of
inserting handlers into an existing Sub-Store site block.

If users may still open an old Sub-Store host page path, set
`HUB_LEGACY_REDIRECT_HOSTS` in the live env. The deploy script will add only a
path-specific 302 redirect for the hub page path, leaving the rest of that host
owned by its existing service.

## Runtime Env

The live runtime env is expected at:

```text
/opt/frontier/subscription-hub/secrets/hub.runtime.env
```

Use `examples/hub.runtime.env.example` as a template. The live file must be
`0600` and owned by root.
