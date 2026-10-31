# API reference (v1)

Base path `/api/v1`. Interactive OpenAPI docs are served by the backend at `/api/docs` (schema at `/api/openapi.json`).

**Auth.** Sign in through `/auth/login`, `/auth/register` or `/auth/demo`. Each sets an `HttpOnly` `tm_session` cookie and also returns a JWT, usable as `Authorization: Bearer <token>`.

**Authorization.** Every project-scoped resource checks membership. Non-members receive **404** (existence is not disclosed). Members without the required role receive **403**.

| Role | Can |
|---|---|
| viewer | read everything in the project |
| analyst | + run analyses, ask the assistant, give feedback, change incident status, generate reports |
| editor | + upload telemetry |
| owner | + project settings and membership |

**Errors** always use one envelope:

```json
{ "error": { "code": "validation_error", "message": "Request validation failed.", "details": [{"loc": ["body","email"], "msg": "..."}], "request_id": "4f0c..." } }
```

Common codes: `unauthenticated` (401), `invalid_token` (401), `forbidden` (403), `not_found` (404), `email_taken` (409), `analysis_required` (409), `payload_too_large` (413), `unsupported_file` (415), `validation_error` (422), `rate_limited` (429, with `Retry-After`), `timeout` (504).

**Pagination.** List endpoints take `page` and `page_size` and return `{items, total, page, page_size}`.

## Endpoints

### Meta
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | DB connectivity; 503 when degraded |
| GET | `/meta` | algorithm version, demo mode, active writer/model, upload limit |

### Auth
| Method | Path | Body / notes |
|---|---|---|
| POST | `/auth/register` | `{email, password (≥10), display_name}` → 201 session |
| POST | `/auth/login` | `{email, password}` |
| POST | `/auth/demo` | demo session (only when `TRACEMIND_DEMO_MODE=true`) |
| POST | `/auth/logout` | clears cookie |
| GET | `/auth/me` | current user |

Login, registration and demo are rate-limited per client IP.

### Projects
| Method | Path | Role |
|---|---|---|
| POST | `/projects` | any user (demo sessions: max 3 sandbox projects) |
| GET | `/projects` | member projects, with your role |
| GET / PATCH | `/projects/{id}` | viewer / owner. PATCH: name, description, environments, `analysis_settings` (whitelisted keys with bounded ranges) |
| GET | `/projects/{id}/overview?range=1h\|6h\|24h\|7d\|30d\|all&environment=` | dashboard aggregates |
| GET / POST | `/projects/{id}/members` | viewer / owner (`{email, role}`) |
| DELETE | `/projects/{id}/members/{member_id}` | owner; the last owner cannot be removed |

### Ingestion
| Method | Path | Notes |
|---|---|---|
| POST | `/projects/{id}/ingestions/preview` | multipart `file`, `environment`, `service_mapping` (JSON). Validates the first 500 rows, nothing persisted |
| POST | `/projects/{id}/ingestions` | multipart as above + optional `Idempotency-Key` header → **202** job (200 if the key was already used) |
| GET | `/projects/{id}/ingestions` | history (paginated) |
| GET | `/ingestions/{id}` | status, counters, row errors, detection summary |

Accepted fields (aliases in parentheses): `timestamp` (time, ts, @timestamp), `service` (service_name, service.name, app), `type` (log, span, metric, deployment, dependency; inferred if absent), `severity` (level), `message` (msg), `trace_id`, `span_id`, `parent_span_id`, `duration_ms` (duration), `status_code`, `metric`/`value`, `environment`, `attributes` (object or JSON string; `attr.*` CSV columns). Unknown columns are kept as attributes.

### Telemetry & topology
| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{id}/services?environment=` | services with event/error counts and open incidents |
| GET | `/projects/{id}/service-graph?environment=&incident_id=` | nodes (health, depth, callers/callees, affected, leading, blast radius) and edges (calls, errors, p95, origins, evidence event IDs) |
| GET | `/projects/{id}/events?service=&event_type=&severity=&trace_id=&q=&start=&end=` | normalised events, newest first |
| GET | `/events/{id}` | full event detail |

### Incidents & analysis
| Method | Path | Notes |
|---|---|---|
| GET | `/projects/{id}/incidents?status=&severity=&environment=&q=&sort=started_at\|severity\|status\|title&order=` | table data incl. leading hypothesis and analysis status |
| GET / PATCH | `/incidents/{id}` | PATCH `{status}` (analyst) |
| GET | `/incidents/{id}/timeline` | chronological observed entries with event IDs |
| GET | `/incidents/{id}/anomalies` | detections with baselines and supporting events |
| GET | `/incidents/{id}/metrics` | per-service error-rate / p95 series around the window + deployments |
| GET | `/incidents/{id}/hypotheses?run_id=` | latest completed (or given) run with ranked hypotheses and evidence |
| POST | `/incidents/{id}/analyze` | `{strategy}` → **202** run (rate-limited: 6/min/user by default) |
| GET | `/incidents/{id}/analysis-runs` | run history |
| GET | `/analysis-runs/{id}` | status, stage, progress, summary, versions |
| GET | `/analysis-runs/{id}/events` | **SSE** `progress` events until completed or failed |
| POST | `/incidents/{id}/investigate` | `{question}` → answer, cited events, uncertainties, checks, `insufficient_evidence`, writer (20/min/user) |
| GET | `/incidents/{id}/investigations` | Q&A history |
| POST / GET | `/incidents/{id}/feedback` | `{verdict, hypothesis_id?, analysis_run_id?, comment}` |
| POST / GET | `/incidents/{id}/reports` | generate (requires a completed analysis) / list |
| GET | `/projects/{id}/reports` | all reports in a project |
| GET | `/reports/{id}` · `/reports/{id}/markdown` | JSON / `text/markdown` download |

### Evaluation
Benchmarks use synthetic scenarios only and are not project-scoped.

| Method | Path | Notes |
|---|---|---|
| GET | `/evaluation/strategies` | configurations and weights |
| POST | `/evaluation-runs` | `{seed, scenario_count ≤ 60, strategies?}` → **202** |
| GET | `/evaluation-runs` · `/evaluation-runs/{id}` | runs and aggregate metrics |
| GET | `/evaluation-runs/{id}/results?strategy=` | per-scenario rows |
| GET | `/evaluation-runs/{id}/export?format=json\|csv` | machine-readable export |

## Example

```bash
curl -s -c jar -X POST localhost:8000/api/v1/auth/register -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a-long-password","display_name":"Me"}'
```

```bash
curl -s -b jar -X POST localhost:8000/api/v1/projects -H 'content-type: application/json' -d '{"name":"Shop"}'
```

```bash
curl -s -b jar -X POST localhost:8000/api/v1/projects/$PID/ingestions -H "Idempotency-Key: $(uuidgen)" \
  -F file=@samples/shopflow-sample.jsonl -F environment=production
```
