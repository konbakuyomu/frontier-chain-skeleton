# Fleet Registry

`fleet/` is a public-safe, local-only description of fleet intent. It records stable IDs,
roles, protocol capability intent, evidence tiers, and client-publication eligibility. It is
not host runtime state and does not connect to a host.

## Commands

Run these commands from the repository root:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python fleet/fleetctl.py validate --registry fleet/registry.json
python fleet/fleetctl.py plan --registry fleet/registry.json
python fleet/fleetctl.py status --registry fleet/registry.json --host host-bare-data-01
python fleet/fleetctl.py inventory-plan --registry fleet/registry.json --host host-bare-data-01
```

`validate` prints only entity counts. `plan` validates first and writes deterministic JSON to
standard output only. `status` emits the existing public-safe plan fields for exactly one normalized
stable host ID and labels that result as local evidence. `inventory-plan` emits the same status plus
the future remote-inventory gate for that one host.

All four commands are local-only: they create no files, read no ignored configuration, use no
network, and change no host. `inventory-plan` does not resolve a private mapping, SSH alias, or
endpoint; it explicitly reports that mapping resolution and remote contact were not performed. For a
non-retired host, the next gate is an approved explicit-host remote adapter with DIRECT, key-only,
tmux, provider-recovery, and redacted host-readback proof. A retired host stays blocked for both
inventory and promotion. A local plan never promotes stale evidence, changes publication
eligibility, or proves output, data-plane, or physical-client state.

## Data Model

`registry.json` contains sorted arrays of:

- `policies`: one fixed protocol and publication contract per supported host role.
- `hosts`: an opaque `host_id`, role, lifecycle, region tag, policy reference, and public label.
- `endpoints`: an opaque `endpoint_id`, one protocol, desired and observed capability states, and
  an evidence tier.
- `routes`: one opaque `role_id` targeting exactly one endpoint with a purpose and region label.
- `publications`: one relation from a route to `final-mihomo`,
  `shadowrocket-ordinary-uri`, or `shadowrocket-hy2-only`.

The validator keeps the shared final Mihomo, Shadowrocket ordinary URI, and Shadowrocket HY2-only
boundaries separate. A bare sing-box data-plane record must include Reality and declared HY2
capability. Every VMess endpoint is compatibility-only. Control-plane, business, legacy, and public
gateway roles cannot enter client publications through this registry.

Evidence is deliberately not a live claim. A `snapshot`, `historical`, or `unknown` tier is shown
as stale or unknown in the plan and makes a publication ineligible until a separately approved
workflow records fresh evidence.

## Adding A Host

1. Reuse the policy for the intended role and add only normalized stable IDs and public labels.
2. Add endpoints with protocol, desired state, observed state, and evidence tier; keep every array
   sorted by its stable ID.
3. Add a route only for one endpoint, then add only channels allowed for that endpoint protocol and
   host role.
4. Run both commands above before proposing any later inventory, publication, or host-promotion
   work.

Do not add an address, port, URI, credential, certificate, token, SSH setting, or host-local path.
Those values remain host-local or in ignored runtime state. This package is intentionally a
one-shot coordination CLI, not an inventory client, deployment tool, daemon, or controller.
Duplicate JSON fields are invalid rather than silently allowing a later field to replace an earlier
value.
