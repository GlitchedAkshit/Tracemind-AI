# Deployment guide

> Status: the Docker Compose stack has been built and verified locally (PostgreSQL 17, migrations, seeding, Playwright e2e). **No public deployment has been performed from this repository.** The steps below describe a supported path; none of them have been executed.

## Topology

```
Browser ──> Next.js (Vercel or container) ──rewrite /api/v1──> FastAPI container ──> Managed PostgreSQL
                                                                     └──> S3-compatible bucket (optional)
```

The browser never calls the API host directly. Next.js proxies `/api/v1/*`, so the session cookie is first-party and CORS stays closed.

## 1. Database

Create a managed PostgreSQL 15+ instance (Neon, Supabase, RDS, Cloud SQL, …). The connection string format is:

```
TRACEMIND_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require
```

## 2. API (any container host: Fly.io, Render, Railway, Cloud Run, ECS)

Build from `backend/Dockerfile`. On start, the container runs `alembic upgrade head`, then `uvicorn` on `$PORT` (default 8000). Required environment:

```env
TRACEMIND_ENV=production
TRACEMIND_DATABASE_URL=...
TRACEMIND_SECRET_KEY=<64 random chars>      # startup fails if left at the dev default
TRACEMIND_COOKIE_SECURE=true
TRACEMIND_CORS_ORIGINS=["https://your-web-host"]
TRACEMIND_DEMO_MODE=true                     # public demo with synthetic data only
TRACEMIND_SEED_ON_STARTUP=true
```

Optional:

```env
TRACEMIND_STORAGE_BACKEND=s3
TRACEMIND_S3_BUCKET=...
TRACEMIND_S3_REGION=...
TRACEMIND_S3_ENDPOINT_URL=...   # for R2 / MinIO
# add boto3 to requirements.txt when using S3

TRACEMIND_LLM_PROVIDER=anthropic
TRACEMIND_LLM_API_KEY=...       # keep it in the host's secret store
```

Run **one** instance (the job pool and rate limiter are in-process), or add a queue and Redis first. The health check is `GET /api/v1/health`. Local uploads need a persistent volume mounted at `/data`, or use S3.

To regenerate the demo with fresh timestamps:

```bash
python -m app.seed --reset
```

## 3. Web app

**Vercel.** Import the `frontend` directory. Set `BACKEND_URL=https://your-api-host` for both build and runtime, because rewrites are compiled at build time. Build command: `npm run build`.

**Container.** Build from `frontend/Dockerfile` with `--build-arg BACKEND_URL=https://your-api-host`. It runs the Next.js standalone server on port 3000.

## 4. Verify

1. `GET https://api…/api/v1/health` returns `{"status":"ok"}`.
2. Open the web app, choose the demo, and check that the overview shows data and the demo badge.
3. Run the e2e suite against it:

```bash
cd frontend && E2E_BASE_URL=https://your-web-host npx playwright test
```

## CI

`.github/workflows/ci.yml` runs:

- backend lint and tests,
- the migration upgrade/check/downgrade/upgrade cycle on a PostgreSQL service,
- a 3-scenario benchmark smoke run,
- frontend lint, typecheck and build, and
- the full Compose stack with Playwright.

It has not been run on GitHub yet; the same steps pass on a fresh clone in Linux containers.
