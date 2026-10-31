# Technical report outline (target 100–120 pages)

Page budgets are approximate. Items marked ◆ can reuse material already in this repository (docs, tests, benchmark JSON, screenshots).

**Front matter (6 pp).** Title, certificate/declaration, acknowledgements, abstract (≈300 words: problem, approach, key held-out results), table of contents, lists of figures/tables, abbreviations.

## 1. Introduction (8 pp)
1.1 Incidents in distributed web systems; cost of slow diagnosis (MTTR)
1.2 Problem statement: correlate heterogeneous telemetry and rank root causes with verifiable evidence
1.3 Objectives and scope (what TraceMind does and deliberately does not do: no automated remediation)
1.4 Contributions: robust detector with absolute guards; topology-aware correlation; interpretable ranking with contradiction evidence; citation-validated LLM writer; reproducible fault benchmark
1.5 Report organisation

## 2. Background and literature review (14 pp)
2.1 Observability signals: logs, metrics, traces, change events; OpenTelemetry data model
2.2 Anomaly detection for operational metrics: thresholds, z-score, MAD/robust statistics, seasonal methods, ML detectors
2.3 Root-cause analysis in microservices: graph-based (call-graph propagation, random walks), causal discovery, trace-based, change-correlation approaches
2.4 LLMs for operations (AIOps): summarisation, hallucination, grounding and citation, prompt injection via untrusted data
2.5 Evaluation practice: injected-fault benchmarks, top-k accuracy, limitations of synthetic data
2.6 Gap analysis motivating an evidence-first, interpretable design

## 3. Requirements analysis (8 pp)
3.1 Stakeholders and personas (on-call engineer, SRE lead, reviewer)
3.2 Functional requirements (ingestion → report), traced to pages and endpoints ◆
3.3 Non-functional: correctness, explainability, security, performance, accessibility, reduced motion
3.4 Use cases and user stories; Definition of Done

## 4. System design (14 pp)
4.1 Architecture and deployment views ◆ (`docs/architecture.md`)
4.2 Data model and ER diagram; idempotency strategy ◆ (`docs/database.md`)
4.3 API design: resources, versioning, error envelope, pagination, async jobs and SSE ◆ (`docs/api.md`)
4.4 Security architecture: auth, RBAC, demo isolation, upload validation, redaction ◆ (`docs/security.md`)
4.5 Frontend information architecture and design system (AXON-inspired tokens, typography, motion principles)

## 5. Analysis engine (18 pp)
5.1 Normalisation and canonical event schema; timestamp heuristics; hashing
5.2 Time-series construction; handling sparse and missing data
5.3 Robust anomaly detection: MAD scaling, scale floors, absolute thresholds, baseline contamination guard; worked example
5.4 New error-signature detection; burst onset (with the bug found and fixed)
5.5 Dependency graph construction and error-origin attribution
5.6 Incident correlation (union-find, time and topology predicates, time-only fallback)
5.7 Root-cause ranking: component definitions, weights, contradiction penalty, cause typing, evidence selection, uncertainty generation
5.8 Complexity and performance considerations

## 6. LLM investigation layer (10 pp)
6.1 Provider adapter and structured outputs; fallback policy
6.2 Evidence bundle design: aliasing, bounding, sanitisation
6.3 Prompt-injection threat model and mitigations
6.4 Schema and citation validation; rejection thresholds; alias rewriting
6.5 Assistant: intent classification, retrieval, insufficient-evidence behaviour
6.6 Limitations (no LLM-backed benchmark yet)

## 7. Implementation (12 pp)
7.1 Technology choices and versions (FastAPI, SQLAlchemy 2, Alembic, pandas, Next.js 16, Tailwind 4, shadcn/ui, React Flow, Recharts, Three.js)
7.2 Backend structure and job execution
7.3 Frontend: session handling with Suspense under Cache Components, investigation workspace, evidence inspector
7.4 GPU fluid particle system: solver passes, shader displacement, tiered fallbacks, performance safeguards
7.5 DevOps: Docker images, Compose, CI pipeline ◆

## 8. Testing (8 pp)
8.1 Strategy: unit, API, migration, end-to-end
8.2 Backend test matrix (malformed telemetry, timestamps, duplicates, anomaly maths, graph, ranking, citations, authorization, LLM failure, empty/noisy data, missing traces, multiple faults) ◆
8.3 Playwright workflows and console-error assertion ◆
8.4 Defects found through testing and their fixes

## 9. Evaluation (14 pp)
9.1 Fault generator design and ground-truth definition ◆
9.2 Metrics and protocol (development vs held-out seeds) ◆
9.3 Results: accuracy by configuration, by fault type, single vs multi-fault ◆
9.4 Ablation discussion (dependency awareness vs trace origins vs deployments vs contradictions)
9.5 Anomaly precision/recall analysis and error cases
9.6 Latency and scalability measurements
9.7 Threats to validity

## 10. User interface walkthrough (6 pp)
Annotated screenshots of every page (overview, ingestion, dependency explorer, incident explorer, investigation workspace, assistant, evaluation lab, reports, settings); accessibility and reduced-motion behaviour.

## 11. Conclusion and future work (4 pp)
Summary of outcomes against objectives; future work: incident splitting for concurrent faults, calibrated confidence, seasonality-aware baselines, OTLP ingestion, durable queues, LLM-backed evaluation, feedback-driven weight learning.

**References (3 pp). Appendices (10 pp).** A: API listing. B: database DDL. C: configuration reference (`.env.example`). D: benchmark raw tables. E: test listing. F: user manual / demo script ◆.
