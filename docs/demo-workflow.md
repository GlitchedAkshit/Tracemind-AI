# Demo workflow (≈8 minutes)

Preparation:

```bash
docker compose up --build
```

Open http://localhost:3000. For fresh timestamps, regenerate the demo:

```bash
docker compose exec backend python -m app.seed --reset
```

| # | Step | What to show / say |
|---|---|---|
| 1 | **Login** | Move the cursor across the particle ring: a GPU fluid field pushes the particles and they settle back. Click *Explore the synthetic demo project*; the data is clearly labelled synthetic |
| 2 | **Overview** | 3 active incidents, error-rate and p95 charts with two clear spikes, service health, anomaly feed, recent deployments. Everything comes from the API; the range and environment selectors re-query |
| 3 | **Telemetry** | Ingestion history (20,964 accepted rows) and the event browser. Point out that the demo role cannot upload |
| 4 | **Dependencies** | Topology derived from spans; click an edge to see the span events it came from. Pick the payments incident to highlight the affected services and the leading hypothesis |
| 5 | **Incidents** | Sort by severity, search, filter. Note the *leading hypothesis* and *analysis* columns |
| 6 | **Investigation: payments incident** | The summary (deterministic writer, citations checked). Ranked hypotheses: **payments-service, deployment regression**, ranked first. The catalog-service decoy deployment ranks low because it is "deployed but showed no anomaly of its own". Open the score components |
| 7 | **Evidence** | Click the deployment evidence: the drawer shows the raw event (v2.0.5, commit, timestamp). Then show the contradicting evidence on checkout-service: failing traces originate in payments-service |
| 8 | **Timeline & charts** | Deployment, then first errors, then the new `NullPointerException` signature, then caller errors. The chart shades the incident window and marks deployments |
| 9 | **Assistant** | Ask "What changed immediately before the first failure?" and "What evidence contradicts it?". Then ask about a service that isn't involved, such as *kafka-service*, to show the explicit *insufficient evidence* answer |
| 10 | **Second incident** | inventory-db: resource saturation, with connection-pool utilisation as evidence and no deployment involved |
| 11 | **Re-analyse with a different strategy** | Choose `temporal` and re-run to watch the progress panel (particles plus SSE stages). Compare how the ranking changes |
| 12 | **Report** | *Export report*: download the Markdown postmortem; show it on the Reports page |
| 13 | **Evaluation lab** | Run 5–10 scenarios, or show the recorded seed-42 table from docs: hybrid 87 % top-1 vs 54 % for the rules baseline, with the ablations. Mention the multi-fault limitation honestly |
| 14 | **Uploads (optional)** | Register, create a project, upload `samples/malformed-sample.jsonl` (rejected rows with reasons, redaction, a flagged prompt-injection message), then `samples/shopflow-sample.jsonl` → incident → analysis |

Talking points:

- Observed vs inferred labelling.
- Scores are not probabilities.
- Every claim is clickable back to telemetry.
- It works with no LLM; when one is enabled its citations are validated.
