# Architecture

## System overview

```mermaid
flowchart LR
  subgraph Browser
    UI[Next.js App Router UI<br/>React Flow · Recharts · Three.js]
  end
  subgraph Web[Next.js server]
    RW[/api/v1/* rewrite/]
  end
  subgraph API[FastAPI]
    R[Routers /api/v1] --> AUTH[Auth + project RBAC]
    R --> SVC[Services]
    SVC --> JOBS[Job pool<br/>ingestion · analysis · evaluation]
    JOBS --> ENG[Analysis engine<br/>pure Python / NumPy / pandas]
    JOBS --> LLM[LLM layer<br/>bundle → model → validation]
    SVC --> REP[Markdown report renderer]
  end
  DB[(PostgreSQL<br/>JSONB attributes)]
  OBJ[(Object storage<br/>raw uploads)]
  CLAUDE[(Claude API<br/>optional)]

  UI -->|same-origin fetch, httpOnly cookie| RW --> R
  SVC --> DB
  JOBS --> DB
  SVC --> OBJ
  LLM -. structured JSON .-> CLAUDE
```

* The browser only talks to its own origin. Next.js rewrites `/api/v1/*` to FastAPI, so the session cookie stays first-party (`HttpOnly`, `SameSite=Lax`). Bearer tokens are also accepted for scripts.
* The analysis engine (`backend/app/analysis`) has **no database dependency**: it consumes `Event` dataclasses. The API, the seed and the benchmark therefore all run the exact same code.
* Expensive work (ingestion, analysis, benchmarks) runs asynchronously. Endpoints return a job/run ID; progress is persisted and exposed by polling and by server-sent events (`/analysis-runs/{id}/events`).

## Data flow

```mermaid
sequenceDiagram
  participant U as User
  participant API
  participant Store as Object storage
  participant DB
  participant Job as Job pool
  U->>API: POST /projects/{id}/ingestions (file, env, mapping, Idempotency-Key)
  API->>Store: put raw file
  API->>DB: ingestion_job (queued)
  API-->>U: 202 job
  API->>Job: run_ingestion
  Job->>DB: parse → validate → normalise → dedupe → insert telemetry_events
  Job->>DB: detect_project: anomalies, incidents, incident_events, timeline
  U->>API: POST /incidents/{id}/analyze
  API->>DB: analysis_run (queued) → 202
  Job->>DB: graph → rank → hypotheses + hypothesis_evidence
  Job->>Job: evidence bundle → LLM (optional) → schema + citation validation
  Job->>DB: summary, model_version, status=completed
  U->>API: GET /analysis-runs/{id}/events (SSE progress)
```

## Analysis pipeline

```mermaid
flowchart TB
  E[Normalised events] --> S[Bucketed series per service<br/>request count · error rate · p95 · explicit metrics]
  S --> A1[Robust detector<br/>median/MAD + absolute thresholds]
  E --> A2[New error-signature bursts]
  E --> G[Dependency graph<br/>parent/child spans + declared deps]
  A1 & A2 --> C[Correlation<br/>time window ∧ graph distance / shared traces]
  G --> C
  C --> I[Candidate incidents + timeline + linked deployments]
  I --> RK[Ranking: components + contradictions]
  G --> RK
  RK --> H[Hypotheses with supporting / contradicting evidence]
  H --> B[Bounded evidence bundle E01..En]
  B --> W{LLM configured?}
  W -- yes --> M[Claude JSON-schema output] --> V[Schema + citation validation]
  W -- no / fails --> D[Deterministic writer]
  V -- rejected --> D
```

See [algorithms.md](algorithms.md) for the maths and thresholds.

## Frontend

| Route | Purpose |
|---|---|
| `/login` | Demo entry, sign in / register, particle ring centrepiece |
| `/` | Overview: particle hero, stats, severity distribution, error-rate & latency charts, service health, anomaly feed, deployments, recent investigations |
| `/ingest` | Drag-and-drop upload, preview, mapping, progress, history, normalised event browser |
| `/services` | React Flow dependency explorer, incident highlighting, node detail, edge evidence |
| `/incidents` | Searchable, filterable, sortable, paginated incident table |
| `/incidents/[id]` | Investigation workspace: summary, ranked hypotheses, score components, supporting/contradicting evidence, limitations, checks, charts with deployment markers, timeline, scoped graph, assistant, anomalies |
| `/evaluation` | Benchmark runner and results across configurations |
| `/reports` | Generated Markdown reports, preview and download |
| `/settings` | Project creation, environments, analysis thresholds, members and roles |

Every evidence reference opens a drawer that loads `/events/{id}` and shows timestamp, service, type, severity, IDs, the verbatim (untrusted) message, attributes and ingestion provenance.

Authenticated routes render their chrome immediately. Pages suspend on the client-side session (`useSession` uses React `use()` on a session promise) behind per-segment `loading.tsx` boundaries. This keeps Next 16 Cache Components / instant-navigation validation satisfied.

### Particle system

`ParticleField.tsx` builds about 26k points on a hollow, flattened, 1.5-turn twisted torus with varying thickness. Rendering uses one of three tiers:

1. **Fluid** (WebGL2 with `EXT_color_buffer_float` and vertex texture fetch): a 128² half-float stable-fluids solver (advect → divergence → 12 Jacobi pressure iterations → gradient subtract, with dissipation). The pointer splats velocity into the field. The vertex shader samples the field at each particle's screen position and offsets it, so particles flow and then settle back as the field decays.
2. **CPU**: per-particle spring and repulsion.
3. **Static**: an SVG ring.

The camera eases toward the pointer. Rendering pauses off-screen and in hidden tabs, renders one still frame under reduced motion, and drops to a lower tier on context loss.
