# ATT public SOCKS5 / HTTP gateway

This directory owns a standalone, authenticated Mihomo mixed gateway for the fixed ATT
egress role. Recipients use a four-field handoff and select a client-compatible proxy mode;
they do not receive subscription URLs, upstream node data, or egress credentials.

## Client contract

- Preferred TLS entry: `edge-us.konbakuyomu.us:8443`.
- In a client with an `HTTPS` protocol dropdown but no separate `Proxy TLS` switch, including
  Roxy Browser, select `HTTPS` on `8443`. This is TLS to the proxy followed by authenticated
  HTTP CONNECT.
- In a client with a separate `Proxy TLS` switch, select `HTTP` or `SOCKS5` and enable it:
  `HTTP+TLS` or `SOCKS5+TLS`.
- Do not select plain `SOCKS5` on `8443`. It sends raw SOCKS5 to a TLS listener, which is a
  protocol mismatch.
- Legacy raw fallback: the handoff's `public-host:34567`, select plain `SOCKS5` or `HTTP` and
  leave `Proxy TLS` off. This listener is plaintext.
- Both listeners use the same per-recipient username/password and route only to `ATT`.
- TCP only: UDP, QUIC, and UDP relay are not supported.
- Do not stack a system proxy, a subscription proxy, and this proxy at the same time. When
  Sparkle TUN is active on Windows, keep the public gateway hostname in a local DIRECT
  override so the connection to the gateway cannot loop back through ATT.
- Use HTTPS destinations through the legacy listener and treat it as a compatibility or
  diagnostic path, not the preferred mainland-client path.

The generated rules remain fail-closed: each authenticated username has an `IN-USER -> ATT`
rule and the terminal rule is `MATCH,REJECT`. There is no `DIRECT` or alternate-node fallback.

## Runtime boundary

All mutable state stays in the Git-ignored `.runtime/` directory. It is created with `0700`;
state, handoff, and certificate files are `0600` where applicable. Do not copy this directory
into source control, task notes, chat, or ordinary logs.

Certbot state is deliberately scoped to:

```text
.runtime/certbot/config
.runtime/certbot/work
.runtime/certbot/logs
```

`config` holds Certbot's account material and the `live/ -> archive/` certificate links. Mihomo
mounts the whole directory read-only, not individual `live` files, so those links remain valid
inside the container. The manager rejects missing, expiring, hostname-mismatched, mismatched-key,
non-RSA-2048, or non-ISRG-Root-X1-chain material before a deploy or reconcile can recreate the
public TLS listener.

## Certificate transport

The one-shot Certbot container is pinned to:

```text
certbot/certbot:v5.7.0@sha256:d07bd043d61d6bee1114235ac12c2e9a5c54b6931b3ccf5e1174d6c8c4afaa95
```

It runs only while issuing or renewing, uses the stable name `att-certbot-acme`, and joins the
existing external Docker network `edge_ingress`. It has no published host ports. Certbot's
standalone listener is `0.0.0.0:18081` inside that Docker network, so Caddy is its only intended
caller.

Before initial issuance, add the narrowly scoped handler from
`templates/certbot-http01.caddyfile` to the existing **HTTP port 80** Caddy site for
`edge-us.konbakuyomu.us`. Put it before any redirect or catch-all handler. It preserves the full
challenge path and proxies only `edge-us.konbakuyomu.us/.well-known/acme-challenge/*` to
`att-certbot-acme:18081`. Do not add a global challenge route, publish `18081`, use host
networking, or open a UFW rule for this challenge listener.

The live Caddy configuration is outside this checkout. Back it up, validate its actual syntax,
and reload only Caddy after the exact handler is in place. This repository does not install or
reload that route automatically.

## Bootstrap and renewal

Run these commands on SJC from the deployment directory as root or through the established
root-only operational path. The registration email must live in a separate root-owned `0600`
one-line file; do not put it in shell history, a unit file, or this repository.

```bash
python3 manage.py prepare-certbot
python3 manage.py issue-tls --email-file <ROOT_ONLY_ACME_EMAIL_FILE>
python3 manage.py check-tls
python3 manage.py reconcile
```

Issuance requests an RSA-2048 certificate for `edge-us.konbakuyomu.us`, uses HTTP-01 standalone
on the internal Docker-network port `18081`, and asks the CA for the `ISRG Root X1` chain. It
recreates Mihomo only after the certificate or key content changed and the replacement passes the
same preflight.

Renewal uses the same one-shot container, name, network, and Caddy route:

```bash
python3 manage.py renew-tls --dry-run
python3 manage.py renew-tls
```

`--dry-run` must not alter material or restart Mihomo. A normal renewal validates the new files,
compares content fingerprints, and leaves the active gateway alone when Certbot reports no change.

Install the supplied root-owned timer only after manual issuance and a successful canary:

```bash
sudo install -o root -g root -m 0644 systemd/att-proxy-share-certbot-renew.service \
  /etc/systemd/system/att-proxy-share-certbot-renew.service
sudo install -o root -g root -m 0644 systemd/att-proxy-share-certbot-renew.timer \
  /etc/systemd/system/att-proxy-share-certbot-renew.timer
sudo systemctl daemon-reload
sudo systemctl enable --now att-proxy-share-certbot-renew.timer
systemctl list-timers att-proxy-share-certbot-renew.timer
```

The timer invokes only `manage.py renew-tls`; it never changes Caddy, UFW, provider firewall,
subscriptions, or the private ATT router.

## Credential commands

```bash
python3 manage.py add friend-a
python3 manage.py list
python3 manage.py status
python3 manage.py rotate friend-a
python3 manage.py remove friend-a
python3 manage.py disable
python3 manage.py enable
python3 manage.py refresh-handoffs
```

`add` and `rotate` print a redacted summary and write the full handoff to
`.runtime/handoff/<label>.txt`. Use the root-only handoff file to configure a generic client;
normal status, list, deployment, and renewal output must not print the username or password.
`show <label> --reveal` is an explicit secret-reveal operation; do not call it from automation,
logs, screenshots, or ordinary verification commands.

## Canary and rollout

1. Record the current container, listener owner, firewall, Caddy configuration, and private ATT
   egress state before changing anything.
2. Stage source and run the local unit tests, Mihomo configuration test, and Compose config check.
3. Add the exact Caddy HTTP-01 handler, validate Caddy, and confirm it is limited to the hostname
   and ACME challenge path above. Do not expose `18081` on the host.
4. Issue with a disposable root-only registration email file. Check the hostname, RSA-2048 key,
   validity, and `ISRG Root X1` chain with `python3 manage.py check-tls`.
5. Create one disposable gateway credential. From an independent external observer, test TLS
   SOCKS5 and HTTP CONNECT against an HTTP target and an HTTPS target, test wrong credentials,
   and confirm the observed exit ISP/ASN is ATT. A TCP connect or TLS handshake alone is not an
   acceptance result.
6. Confirm that the legacy `34567` listener still behaves as before, that no anonymous proxy is
   available, and that the existing three-client subscription path and private ATT egress are
   unchanged.
7. Install the renewal timer only after the external canary is successful. Android SuperProxy and
   Roxy remain user-assisted acceptance checks; do not label them verified from curl alone.

## Rollback

For an ingress incident, use `python3 manage.py disable` or stop only `att-proxy-share`. Do not
restart Caddy, Sub-Store, the private ATT router, or unrelated edge services.

For certificate-route rollback, first stop the one-shot Certbot invocation if it is running, then
remove only the exact Caddy HTTP-01 handler after restoring the prior Caddy configuration. Keep
the existing certificate archive and runtime backup available; do not recursively delete
`.runtime/certbot`, run Docker prune, or bulk-remove Docker data. Restore the previous allowlisted
source/configuration, validate it, and recreate only `att-proxy-share` when a tested rollback is
required.
