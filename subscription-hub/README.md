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
  --registry .\subscription-hub\rules.json `
  --contract .\ai-routing-contract.json

python .\scripts\render-ai-routing.py --check
```

The live host runs `subscription-rule-mirror.timer` every 6 hours. The mirror
files are public client resources under `/rules/*`; the status page only records
rule ids, source hosts, byte counts, and hashes.

## AI Routing Source Maintenance

`ai-routing-contract.json` and its JSON Schema own official OpenAI, Anthropic,
and xAI coverage. Anthropic first-party hosts must remain covered by the local
inline baseline (`DOMAIN-SUFFIX,anthropic.com`), not only by the mirrored
`ai-anthropic` RULE-SET. Private `ANTHROPIC_BASE_URL` gateways stay out of git:
pass `extra_ai_api_hosts` in the Sub-Store Script Operator, or load
`extra-ai-hosts.local.js` before `main.js` on Sparkle. `rules.json` owns
replaceable community sources, and every mirrored item must declare a boolean
`enabled` state explicitly. Client files consume only these stable logical
providers: `ai-openai`, `ai-anthropic`, `ai-xai`, and `ai-community-supplement`.

To replace a source without changing client URLs:

1. Keep `logical_provider` and `file` unchanged; update `source`,
   `semantic_source`, and the adapter review fields in `rules.json`.
2. Sync into an ignored staging directory and inspect `rules/status.json`.
   Unknown attributes/regex, missing sentinels, forbidden broad rules, or an
   abnormal normalized diff retain last-known-good and stop publication.
3. Run `rule_mirror.py --check`, `render-ai-routing.py --check`, and the AI
   regression tests. A source-only replacement must leave both generated
   client blocks byte-identical.
4. Deploy the new logical mirror files before switching or refreshing clients.
   Keep disabled legacy endpoints for the agreed rollback window; do not delete
   them as part of source promotion.

The generated status reports active and disabled counts from the registry. Do
not restore fixed `18/16/34` assumptions in renderers or verifiers.

## Deploy Shape

Use the two-stage path for rule-source changes. The first command publishes
only the registry, contract, adapter, systemd unit, and mirrored rule bodies;
it does not replace `shadowrocket.conf`, public hub pages, or ingress files:

```powershell
.\subscription-hub\deploy.ps1 -RulesOnly `
  -EnvFile .\subscription-hub\.secrets.local\hub.runtime.env

.\subscription-hub\deploy.ps1 -RulesOnly -Apply `
  -EnvFile .\subscription-hub\.secrets.local\hub.runtime.env `
  -SshHost <VPS_PUBLIC_IP> `
  -SshPort <SSH_PORT> `
  -SshUser root `
  -SshKey <PRIVATE_KEY_PATH>
```

Verify `ok == total`, `stale == failed == 0`, matching registry-derived
counts, and `semantic.status=passed` for every logical provider before the
full client-facing deployment:

```powershell
.\subscription-hub\deploy.ps1 `
  -SshHost <VPS_PUBLIC_IP> `
  -SshPort <SSH_PORT> `
  -SshUser root `
  -SshKey <PRIVATE_KEY_PATH>
```

Default mode is dry-run. Add `-Apply` only after the plan looks correct.
Apply modes launch the remote transaction in the persistent `ai` tmux session
with an outer deadline and require terminal result, gate, cleanup, window, and
stage-residue markers. A missing terminal marker is incomplete, not success;
the run-owned stage is preserved rather than recursively deleted.

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
