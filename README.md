# NeuroIONM / IONM Platform

Clinical intraoperative neuromonitoring (IONM) workspace built with **FastAPI**, **MongoDB** and a vanilla JavaScript frontend. It provides a knowledge tree, case planning, body-map guidance, an academy layer and an admin knowledge-ingestion workflow.

> **Clinical note:** This software is a decision-support workspace. It does not replace clinical judgement, diagnosis or the responsible physician.

## Features

- **IONM Live** — case planning, channel/electrode suggestions, body map and structured clinical readiness.
- **Academy** — curriculum, lesson packages, checkpoints, scored scenarios and electrode-placement labs.
- **Admin** — user profiles, PDF/OCR knowledge ingestion and body-map coordinate curation.
- **Traceability** — source citations, visible gaps and a knowledge-tree explorer.
- **AI assistance** — optional retrieval-augmented explanations with evidence panels; no automatic clinical decisions.

## Stack

- Python 3.12
- FastAPI + Gunicorn/Uvicorn
- MongoDB
- PyMuPDF, Tesseract OCR
- HTML/CSS/JavaScript frontend
- Optional DeepSeek/Jina/PubMed integrations via environment variables

## Repository layout

```text
backend/app/
  api/          API routes
  core/         configuration and shared services
  db/           MongoDB access
  models/       domain models
  routes/       frontend/API route groups
  scripts/      seed, indexing and preflight scripts
  seed_data/    IONM seed knowledge
  services/     planning, RAG, auth, telemetry and validation
frontend/       Live, Academy, Admin, login and documentation UI
deploy/         production deployment templates
docs/           architecture and handoff notes
tools/          data extraction and smoke-test utilities
tests/          backend and UI contract tests
```

## Local setup

Requirements: Python 3.12 and a local MongoDB instance.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create `.env` from `deploy/neuroionm/env.production.template` and fill in local values. Do **not** commit `.env` or real API keys.

```bash
python -m backend.app.scripts.seed_db
python -m backend.app.scripts.index_embeddings
python -m uvicorn backend.app.main:app --reload --reload-dir backend --reload-dir frontend --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/live.html
http://127.0.0.1:8000/academy.html
http://127.0.0.1:8000/admin.html
```

## Environment

The production template documents the expected variables, including:

- `IONM_ENV`, `IONM_DEBUG`
- `IONM_MONGO_URI`, `IONM_MONGO_DB`
- `IONM_FRONTEND_DIR`, `IONM_ASSETS_DIR`
- `IONM_CORS_ORIGINS`, `IONM_ALLOWED_HOSTS`
- `IONM_ADMIN_SESSION_SECRET`
- `IONM_ADMIN_BOOTSTRAP_USERNAME`, `IONM_ADMIN_BOOTSTRAP_PASSWORD_HASH`
- optional `DEEPSEEK_API_KEY`, `JINA_API_KEY`, `PUBMED_API_KEY`

There is **no default admin password** in this repository. In development, an optional local admin can be supplied through:

```text
IONM_DEV_ADMIN_USERNAME
IONM_DEV_ADMIN_DISPLAY_NAME
IONM_DEV_ADMIN_PASSWORD
```

Production admin accounts must be configured through environment/bootstrap settings or the configured account JSON.

## Docker

```bash
docker build -t neuroionm .
docker run --rm -p 8000:8000 --env-file .env neuroionm
```

## Tests

```bash
pytest
```

Some tests require MongoDB. Live external-provider tests are opt-in and must never be run with production credentials.

## Security

- Patient/clinical data must be handled according to the applicable governance and privacy policy.
- Never commit `.env`, credentials, patient records, database dumps or uploaded documents.
- Use a strong `IONM_ADMIN_SESSION_SECRET`.
- Restrict `IONM_ALLOWED_HOSTS` and CORS to the actual deployment domains.
- Keep MongoDB and administrative interfaces private.

## Documentation

See `docs/` for deployment, administration and handoff notes. The public repository contains source code only; operational secrets and production data are intentionally excluded.
