# Resume description

Every statement below describes implemented, tested functionality or a recorded result from this repository.

## One-liner

**TraceMind AI**: full-stack incident-investigation platform that correlates logs, traces, metrics and deployments to rank root causes with verifiable evidence (FastAPI, PostgreSQL, Next.js, Three.js).

## Bullet version

- Built an evidence-driven incident-investigation platform (FastAPI, SQLAlchemy/Alembic, PostgreSQL JSONB, Next.js 16, TypeScript, Tailwind, React Flow, Recharts). It ingests JSONL/CSV telemetry with validation, timestamp normalisation, deduplication, redaction and per-row rejection reporting.
- Implemented a robust anomaly detector (rolling median/MAD scores with absolute thresholds and burst-based new-error-signature detection), topology-aware incident correlation, and an interpretable root-cause ranker. It combines temporal, dependency, trace-origin, error and deployment evidence with a contradiction penalty, and stores score components with supporting and contradicting events.
- Designed a grounded LLM summary layer with the Claude API: bounded, aliased evidence bundles; JSON-schema structured output; prompt-injection flagging; citation validation that rejects fabricated references; and a deterministic fallback so the product works without a model.
- Created a seeded fault-injection benchmark with 4 fault types, noise and multi-fault scenarios, plus ablation studies. On a held-out set of 46 injected faults the hybrid ranker reached 87% top-1 and 100% top-3 accuracy, vs 54% top-1 for a rules-only baseline, with 0 invalid citations.
- Delivered project-scoped RBAC, a read-mostly public demo, rate limiting, async jobs with SSE progress, Markdown postmortem export, Docker Compose deployment and GitHub Actions CI. Covered by 62 pytest tests and Playwright end-to-end tests.
- Built an AXON-inspired interface with a WebGL GPU fluid-simulation particle field (stable-fluids solver driving point displacement), with CPU and static fallbacks and reduced-motion support.

## Short paragraph

TraceMind AI is a full-stack observability tool that turns raw logs, traces, metrics and deployment events into ranked, evidence-linked root-cause hypotheses. I built the FastAPI/PostgreSQL backend, a robust-statistics anomaly detector, topology-aware correlation, an interpretable ranking model with contradiction evidence, and a Claude-based summary writer whose citations are validated against real telemetry. I also built a Next.js interface with a GPU fluid particle visual and a reproducible fault-injection benchmark, on which the hybrid ranker reaches 87% top-1 accuracy on held-out synthetic scenarios against a 54% rules baseline.

> Note for interviews: the benchmark is synthetic, multi-fault top-1 is 50%, and no LLM-backed benchmark or public deployment has been run yet. Be ready to discuss those limits.
