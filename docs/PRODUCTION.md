# Production Deployment Runbook

This project is prepared to run as a FastAPI application behind a reverse proxy with MongoDB.

## Deployment Shape

- App container: FastAPI served by Gunicorn + Uvicorn worker.
- Database: MongoDB 7.
- Reverse proxy: Caddy, Nginx, Traefik, Cloudflare Tunnel or a managed proxy.
- TLS: terminate HTTPS at the reverse proxy.
- Secrets: stored only in a server-side `.env.production` file or deployment secret manager.

## Server Files

Create `.env.production` on the server from `.env.production.example`.

The compose file allows syntax validation without this file, but real production deployments must create it before `docker compose up`.

Set these before exposing a domain:

```env
IONM_ENV=production
IONM_CORS_ORIGINS=https://your-real-domain
IONM_ALLOWED_HOSTS=your-real-domain,www.your-real-domain,127.0.0.1,localhost
DEEPSEEK_API_KEY=
JINA_API_KEY=
IONM_ADMIN_SESSION_SECRET=
TESSERACT_LANGUAGES=eng+ell
```

Do not commit `.env.production`.

## Build And Run

```bash
docker compose up -d --build
docker compose logs -f app
```

Health checks:

```bash
curl http://127.0.0.1:8000/api/live
curl http://127.0.0.1:8000/api/ready
curl http://127.0.0.1:8000/api/health
```

All HTML workspaces and non-health APIs are login-gated. Unauthenticated browser requests are redirected to `/login.html`; unauthenticated protected API calls return `401`. Keep `/api/live`, `/api/ready` and `/api/health` reachable for monitoring.

Clinical planner readiness check:

```bash
curl "http://127.0.0.1:8000/api/knowledge/planner?node_key=live.spine.lumbar.disc.l4-l5"
```

For the current seed, this should include `clinical_plan`, 5 channel-plan entries and 10 body-map pins.

## Seed And Preflight

The app can seed Mongo on startup when `IONM_AUTO_SEED_ON_STARTUP=true`.

Manual seed:

```bash
docker compose exec app python -m backend.app.scripts.seed_db
```

Rebuild semantic retrieval index after seed or knowledge-base updates:

```bash
docker compose exec app python -m backend.app.scripts.index_embeddings
```

Admin PDF uploads also rebuild the same semantic retrieval index after OCR/chunking.

Preflight:

```bash
docker compose exec app python -m backend.app.scripts.preflight
```

Automated backend contract tests:

```bash
docker compose exec app python -m pytest -q
```

Expected production preflight basics:

- Mongo connected.
- Seed file present.
- Frontend present.
- Assets present.
- Knowledge nodes, muscles and coordinates are non-zero.
- Knowledge embeddings are non-zero after `index_embeddings` has been run.
- Academy progress/attempt counters are visible so learner persistence can be monitored.

## Reverse Proxy Notes

For the purchased production domain `neuroionm.eu` on the existing Hetzner server, use `docs/NEUROIONM_DEPLOYMENT.md`. That runbook keeps MedResearch files and services untouched, uses `/var/www/sites/neuroionm`, a separate `neuroionm.service`, and a separate Nginx server block.

Proxy traffic to:

```text
http://127.0.0.1:8000
```

Required headers:

```text
Host
X-Forwarded-For
X-Forwarded-Proto
```

Set the real domain in `IONM_ALLOWED_HOSTS`; otherwise FastAPI will reject the host.

## Mongo Backup

At minimum schedule daily backups:

```bash
docker compose exec mongo mongodump --archive=/tmp/ionm.archive --db=ionm_platform
docker compose cp mongo:/tmp/ionm.archive ./backups/ionm-$(date +%F).archive
```

Restore test before relying on backups:

```bash
docker compose cp ./backups/ionm-YYYY-MM-DD.archive mongo:/tmp/ionm.archive
docker compose exec mongo mongorestore --archive=/tmp/ionm.archive --drop
```

## Before Public Go-Live

- Rotate any API keys that were ever pasted into chat or logs.
- Set production `IONM_CORS_ORIGINS` and `IONM_ALLOWED_HOSTS` to the real domain.
- Confirm `/api/ready` returns `200`.
- Confirm `/api/knowledge/planner?node_key=live.spine.lumbar.disc.l4-l5` returns `clinical_plan` with channel and coordinate coverage.
- Confirm `/api/ai/ask` works with the server-side DeepSeek key.
- Confirm `python -m backend.app.scripts.index_embeddings` works from the server network and `/api/ai/ask` returns retrieval evidence with status `ready`.
- Confirm `/admin.html` login works for the hardcoded admin and that Tesseract can OCR uploaded PDFs with `TESSERACT_LANGUAGES=eng+ell` or the production language set.
- Confirm unauthenticated `/`, `/live.html`, `/academy.html`, `/manual.html` and `/admin.html` redirect to `/login.html`, then confirm successful login lands on `/`.
- Confirm the protected landing page links to Live, Academy, Manual and Admin.
- Confirm `/admin.html` body-map curation can load the high-resolution asset, move/save a test coordinate in Mongo and export reviewed coordinates with `python -m backend.app.scripts.export_coords --output ionm-coords-curated.json`.
- Confirm `/academy.html` can select an Admin-created learner, save Mongo progress, grade checkpoint/waveform/case attempts and generate `/api/academy/progress/report`.
- Confirm expanded protocols are seeded and indexed after clinical-content updates.
- Do not mark `bodymap-asset.json` as production-curated until every coordinate has been clinically reviewed against the exact deployed `body.jpg` asset.
- Enable HTTPS at the reverse proxy.
- Confirm Mongo backups are scheduled and restore-tested.