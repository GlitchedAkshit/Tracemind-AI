# Database

PostgreSQL 17 (tested), managed by Alembic (`backend/alembic/versions/0001_initial_schema.py`). Conventions:

- UUID primary keys
- timezone-aware UTC timestamps (`TIMESTAMP WITH TIME ZONE`)
- `JSONB` for flexible attributes
- foreign keys with explicit `ON DELETE`
- check constraints on enums
- unique constraints for idempotency

SQLite is supported for tests through type variants.

```mermaid
erDiagram
  users ||--o{ project_members : has
  projects ||--o{ project_members : has
  projects ||--o{ services : contains
  projects ||--o{ ingestion_jobs : receives
  projects ||--o{ telemetry_events : stores
  services ||--o{ telemetry_events : emits
  ingestion_jobs ||--o{ telemetry_events : produced
  projects ||--o{ incidents : has
  projects ||--o{ anomalies : has
  incidents ||--o{ anomalies : groups
  incidents ||--o{ incident_events : links
  telemetry_events ||--o{ incident_events : linked
  incidents ||--o{ analysis_runs : analysed_by
  analysis_runs ||--o{ hypotheses : ranks
  hypotheses ||--o{ hypothesis_evidence : cites
  telemetry_events ||--o{ hypothesis_evidence : cited
  incidents ||--o{ investigation_reports : documented_by
  incidents ||--o{ assistant_messages : questioned
  incidents ||--o{ feedback : rated
  evaluation_runs ||--o{ evaluation_results : contains

  telemetry_events {
    uuid id PK
    uuid project_id FK
    uuid service_id FK
    uuid ingestion_job_id FK
    string event_type "log|span|metric|deployment|dependency"
    timestamptz occurred_at
    timestamptz ingested_at
    string trace_id
    string span_id
    string parent_span_id
    string severity
    text message
    string metric_name
    float metric_value
    float duration_ms
    int status_code
    jsonb payload
    string event_hash "UNIQUE(project_id, event_hash)"
  }
  incidents {
    uuid id PK
    uuid project_id FK
    string title
    string status "open|investigating|resolved"
    string severity "critical|high|medium|low"
    string environment
    string fingerprint "UNIQUE per project"
    jsonb affected_services
    jsonb timeline
    timestamptz started_at
    timestamptz ended_at
    timestamptz created_at
    uuid latest_analysis_run_id
  }
  analysis_runs {
    uuid id PK
    uuid incident_id FK
    string algorithm_version
    string model_version
    string status "queued|running|completed|failed"
    int progress
    string stage
    jsonb config
    jsonb summary
    text error_message
    timestamptz started_at
    timestamptz completed_at
  }
  hypotheses {
    uuid id PK
    uuid analysis_run_id FK
    int rank "UNIQUE(run, rank)"
    string cause_type
    string service_name
    float ranking_score
    jsonb score_components
    text summary
    text uncertainty
    jsonb suggested_checks
  }
  hypothesis_evidence {
    uuid id PK
    uuid hypothesis_id FK
    uuid event_id FK
    string polarity "supporting|contradicting"
    text explanation
  }
```

## Tables

| Table | Notes |
|---|---|
| `users` | email (unique), PBKDF2-SHA256 hash, `is_demo` flag |
| `projects` | name, unique slug, `environments` (JSONB), `analysis_settings` (JSONB, validated ranges), `is_demo` |
| `project_members` | role ∈ owner/editor/analyst/viewer, unique (project, user). Basis of every authorization check |
| `services` | unique (project, name, environment), kind, first/last seen |
| `ingestion_jobs` | file metadata, sha256, `storage_key` (raw file lives in object storage, not in a row), status, row counters, first 500 row errors, detection summary, unique (project, idempotency_key) |
| `telemetry_events` | normalised events; unique (project, event_hash) makes re-ingestion idempotent. Indexes: (project, occurred_at), (project, trace_id), (service, occurred_at), (project, event_type, occurred_at) |
| `anomalies` | type, metric, baseline, observed, robust score, severity, window, supporting event IDs; unique fingerprint per project |
| `incidents` | grouped anomalies, persisted timeline, fingerprint; index (project, started_at) |
| `incident_events` | event links with role anomaly/deployment/timeline; unique (incident, event) |
| `analysis_runs` | algorithm and model versions, progress/stage, config snapshot, summary + writer metadata |
| `hypotheses` / `hypothesis_evidence` | ranked causes, component scores, polarity-tagged evidence |
| `investigation_reports` | Markdown snapshot with algorithm/model versions |
| `assistant_messages` | question, validated answer, writer metadata |
| `feedback` | verdict on a hypothesis/run (correct, partially_correct, incorrect) |
| `evaluation_runs` / `evaluation_results` | benchmark configuration, aggregate metrics, per-scenario per-strategy rows |

## Idempotency

- **Uploads**: an `Idempotency-Key` header returns the existing job. Identical events are skipped via `event_hash`.
- **Detection**: anomalies are fingerprinted by (service, type, metric, window start). Incidents are matched by time overlap and shared services, then updated in place. Superseded incidents that were never analysed are removed.
- **Analysis**: one in-flight run per incident. A repeated `POST /analyze` returns the active run.

## Commands

```bash
alembic upgrade head      # apply
alembic check             # verify models match migrations
alembic downgrade base    # roll back
```
