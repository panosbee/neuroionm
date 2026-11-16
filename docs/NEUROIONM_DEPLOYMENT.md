# NeuroIONM Deployment Runbook

Domain: `neuroionm.eu`  
Production host: Hetzner server already serving `medresearch-ai.org`  
Goal: deploy NeuroIONM without touching `/var/www/Caroline` or existing MedResearch services.

## Pointer DNS

Use Pointer DNS management unless there is a specific reason to move the zone elsewhere.

Do not change any `medresearch-ai.org` DNS records.

Recommended records at Pointer:

```text
Type  Host/Name  Value
A     @          65.21.194.83
A     www        65.21.194.83
```

Optional after A-record validation:

```text
Type  Host/Name  Value
AAAA  @          2a01:4f9:3b:1be2::2
AAAA  www        2a01:4f9:3b:1be2::2
```

If Pointer asks for nameservers and you keep DNS at Pointer, keep Pointer's default nameservers and use `Διαχείριση DNS` for the records above. Do not enter Hetzner nameservers unless the zone is first created in Hetzner DNS.

If the zone is moved to Hetzner DNS, create a `neuroionm.eu` DNS zone there and use Hetzner's authoritative nameservers:

```text
hydrogen.ns.hetzner.com
oxygen.ns.hetzner.com
helium.ns.hetzner.de
```

The simpler path is Pointer DNS with the A records above.

## Isolation Model

Use a separate application root and service:

```text
/var/www/sites/neuroionm/current
/var/www/sites/neuroionm/shared/.env.production
/var/www/sites/neuroionm/shared/admin_knowledge
/etc/systemd/system/neuroionm.service
/etc/nginx/sites-available/neuroionm
/etc/nginx/sites-enabled/neuroionm
```

Do not modify:

```text
/var/www/Caroline
/etc/systemd/system/medresearch-ai.service
/etc/systemd/system/discovery-api.service
/etc/systemd/system/hypothesis-hub.service
/etc/nginx/sites-enabled/medresearch-ai
/var/lib/mongodb
```

The existing server has no Docker installed, so use a Python virtualenv and systemd.

Recommended backend bind:

```text
127.0.0.1:8200
```

Recommended Mongo database:

```text
neuroionm_platform
```

This shares the local Mongo service but not the MedResearch database.

## Preflight

Run read-only checks first:

```powershell
ssh hetzner "hostname; whoami; date -Is"
ssh hetzner "systemctl is-active nginx mongod medresearch-ai.service discovery-api.service hypothesis-hub.service"
ssh hetzner "ss -lntp | grep 8200 || true"
ssh hetzner "nginx -t"
```

Expected:

- Existing services are `active`.
- Port `8200` has no listener.
- `nginx -t` succeeds. Existing MedResearch duplicate warnings may appear; do not clean them during NeuroIONM deployment.

## Server Setup

Create directories:

```powershell
ssh hetzner "mkdir -p /var/www/sites/neuroionm/current /var/www/sites/neuroionm/shared/admin_knowledge"
```

Copy project files to `/var/www/sites/neuroionm/current` while excluding local secrets and transient data. Prefer `scp`/`rsync` from a clean local tree. Never upload local `.env`, `.venv`, `.pytest_cache`, or `data`.

Create the production env file from `deploy/neuroionm/env.production.template`:

```powershell
scp deploy/neuroionm/env.production.template hetzner:/var/www/sites/neuroionm/shared/.env.production
```

Then edit it directly on the server and set real secrets there only:

```powershell
ssh hetzner "nano /var/www/sites/neuroionm/shared/.env.production"
```

Required before public go-live:

- `IONM_ADMIN_SESSION_SECRET`
- `DEEPSEEK_API_KEY`
- `JINA_API_KEY`
- `PUBMED_API_KEY` and `PUBMED_EMAIL` if available

Install dependencies in the shared production virtualenv. Keep the venv outside `current` so release swaps do not remove the executable path used by systemd:

```powershell
ssh hetzner "cd /var/www/sites/neuroionm/current; python3 -m venv /var/www/sites/neuroionm/shared/.venv; /var/www/sites/neuroionm/shared/.venv/bin/python -m pip install --upgrade pip setuptools wheel; /var/www/sites/neuroionm/shared/.venv/bin/python -m pip install -r requirements.txt"
```

Permissions:

```powershell
ssh hetzner "chown -R www-data:www-data /var/www/sites/neuroionm; find /var/www/sites/neuroionm -type d -exec chmod 755 {} \; ; find /var/www/sites/neuroionm -type f -exec chmod 644 {} \;"
```

Install systemd service:

```powershell
scp deploy/neuroionm/neuroionm.service hetzner:/etc/systemd/system/neuroionm.service
ssh hetzner "systemctl daemon-reload; systemctl enable --now neuroionm.service"
ssh hetzner "systemctl status neuroionm.service --no-pager; curl -fsS http://127.0.0.1:8200/api/ready"
```

The service template sets Gunicorn `--timeout 240` because DeepSeek/Jina-backed clinical endpoints can legitimately exceed the default timeout during first-run or provider latency spikes.

Install Nginx HTTP config:

```powershell
scp deploy/neuroionm/nginx-http.conf hetzner:/etc/nginx/sites-available/neuroionm
ssh hetzner "ln -s /etc/nginx/sites-available/neuroionm /etc/nginx/sites-enabled/neuroionm 2>/dev/null || true; nginx -t; systemctl reload nginx"
ssh hetzner "curl -I -H 'Host: neuroionm.eu' http://127.0.0.1/"
```

Keep the NeuroIONM proxy `proxy_read_timeout`/`proxy_send_timeout` aligned with the app AI timeout; otherwise long structured AI calls can surface as Nginx 504 responses even when the backend is healthy.

## TLS

Only run Certbot after DNS resolves publicly:

```powershell
Resolve-DnsName neuroionm.eu
Resolve-DnsName www.neuroionm.eu
```

Expected A records:

```text
65.21.194.83
```

Then issue a certificate only for NeuroIONM:

```powershell
ssh hetzner "certbot --nginx -d neuroionm.eu -d www.neuroionm.eu"
```

## Post-Deploy Checks

```powershell
curl -I https://neuroionm.eu/
curl -I https://www.neuroionm.eu/
curl -I https://medresearch-ai.org/
curl -I https://medresearch-ai.org/meddiscovery
curl -I https://medresearch-ai.org/hypotheses-hub/openapi.json
ssh hetzner "systemctl is-active nginx mongod medresearch-ai.service discovery-api.service hypothesis-hub.service neuroionm.service"
ssh hetzner "journalctl -u neuroionm.service -n 80 --no-pager -o short-iso"
```

## Rollback

Rollback only NeuroIONM:

```powershell
ssh hetzner "rm -f /etc/nginx/sites-enabled/neuroionm; nginx -t; systemctl reload nginx"
ssh hetzner "systemctl disable --now neuroionm.service || true"
```

Verify MedResearch immediately:

```powershell
curl -I https://medresearch-ai.org/
curl -I https://medresearch-ai.org/meddiscovery
ssh hetzner "systemctl is-active nginx mongod medresearch-ai.service discovery-api.service hypothesis-hub.service"
```