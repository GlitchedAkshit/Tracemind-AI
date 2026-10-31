# Evaluation

All numbers below come from recorded runs of `python -m app.bench.runner`. The raw JSON and CSV are in [`benchmarks/results`](../benchmarks/results).

## Method

**Scenarios** (`backend/app/bench/generator.py`, deterministic per seed): each simulates 60 minutes of a 14-service e-commerce topology plus one declared external dependency, at 22–36 requests/min across 3 routes. That produces about 10–15k events per scenario: parent/child spans, error and slow-request logs, per-minute CPU and connection-pool metrics, and deployments. Then:

- **One injected fault**, starting at minute 32–44 and lasting 8–15 minutes, in a random non-entry service: `bad_deployment` (deployment shortly before plus a new exception signature), `latency_injection`, `resource_exhaustion` (pool metric ramps to about 97 % plus timeouts), or `dependency_down`.
- About 20 % of scenarios have a **second concurrent fault** in a different branch.
- **Noise**: a transient error blip in an unrelated service (35 %), 1–3 decoy deployments (one of them close to the fault 40 % of the time), and logs missing trace IDs (0 / 10 / 30 %).
- Failures propagate to callers (85 % per hop, otherwise retried).

**Ground truth** per fault: the injected service and type, plus the *affected* services. A service counts as affected when ≥ 5 % of its spans during the fault were influenced by it, which is recorded during simulation.

**Metrics:**

- **Top-k accuracy**: the incident overlapping the fault's window (± 2 min) and sharing the most affected services is ranked; a hit means the injected service is within the top k. A missed detection counts as a miss.
- **Anomaly precision / recall**: at (service, time-window) level against all injected behaviour (fault-affected services and noise blips).
- **Invalid-citation rate**: share of cited event IDs (ranking evidence plus writer citations) that are not in the input event set, plus any citation the validator rejected.
- **Latency**: wall-clock time for detection + ranking (+ summary for hybrid) per fault.

**Protocol.** Seed **7** was used during development to find bugs (see below). Seed **42** is the held-out run reported here and was not used for any tuning.

## Results: held-out seed 42 (40 scenarios, 46 faults, 34 single-fault, 12 in multi-fault scenarios)

| Configuration | Top-1 | Top-3 | Single-fault top-1 | Multi-fault top-1 | Mean latency | p95 latency |
|---|---|---|---|---|---|---|
| rules_only (baseline) | 54.3 % | 87.0 % | 61.8 % | 33.3 % | 609 ms | 1113 ms |
| temporal | 30.4 % | 89.1 % | 41.2 % | 0.0 % | 611 ms | 1110 ms |
| dependency_aware | 80.4 % | 97.8 % | 91.2 % | 50.0 % | 607 ms | 1103 ms |
| **hybrid** | **87.0 %** | **100 %** | **100 %** | **50.0 %** | 608 ms | 1063 ms |
| hybrid_no_traces | 84.8 % | 100 % | 97.1 % | 50.0 % | 608 ms | 1036 ms |
| hybrid_no_deployments | 87.0 % | 100 % | 100 % | 50.0 % | 613 ms | 1056 ms |
| hybrid_no_contradictions | 84.8 % | 100 % | 97.1 % | 50.0 % | 612 ms | 1050 ms |

Hybrid by fault type (top-1 / top-3):

| Fault type | n | Top-1 | Top-3 |
|---|---|---|---|
| bad_deployment | 9 | 100 % | 100 % |
| dependency_down | 18 | 94.4 % | 100 % |
| latency_injection | 12 | 75.0 % | 100 % |
| resource_exhaustion | 7 | 71.4 % | 100 % |

Other results:

- **Anomaly detection**: precision 75.7 %, recall 99.0% (detections and truth windows counted once per scenario).
- **Invalid citations**: 0 of 2,890 (deterministic writer).
- **Wall clock**: 114.7 s for the whole run (Windows, Python 3.11, AMD64).

Development seed 7 (30 scenarios, 38 faults), for comparison: hybrid top-1 78.9 %, top-3 100 %, single-fault 100 %; rules_only 36.8 %; anomaly precision 77.5 %, recall 97.5 %.

## Reading the results

- **Dependency awareness is the largest single gain** (temporal 30 % → dependency-aware 80 %). Callers of a failing service go anomalous at almost the same minute, so time alone cannot separate cause from victim.
- **Trace origins and contradictions each add about 2 points** on this set. Removing either costs one single-fault case.
- **The deployment component adds nothing measurable here.** The new error signature and temporal precedence already identify bad deployments in these scenarios, and decoy deployments are mostly neutralised by the "deployed but not anomalous" contradiction. It still matters for explanations: it attaches the deployment event as evidence and drives the cause type.
- **Multi-fault top-1 is capped near 50 %.** Two concurrent faults are usually merged into one incident (they share callers such as `api-gateway`), so only one of them can rank first. Top-3 is 100 %. Splitting concurrent incidents is the clearest next step.
- **Precision of 76 %** mostly reflects real but off-truth detections: propagated symptoms in services below the 5 % "affected" cut-off, and CPU-metric fluctuations.
- **Invalid citations are 0 by construction** for the deterministic writer: it can only cite evidence the ranker selected. The metric matters when an LLM writer is enabled. Validation is covered by unit tests with fabricated and mostly fabricated outputs (`tests/test_llm.py`). No LLM-backed benchmark was run, because no API key was configured.

## Bugs found on the development seed (fixed before the held-out run)

1. Low-volume buckets turned single failures into 10–20 % error-rate spikes. Fix: minimum error count.
2. A new-error-signature window started at a stray background occurrence minutes before the fault, and spanned recurrences across incidents. Fix: burst-based onset and end.
3. The correlation gap of 10 min merged unrelated blips. Fix: 5 min.

## Threats to validity

- Synthetic data is cleaner than production: consistent instrumentation, a single propagation model, and known topology.
- The scenario generator and the algorithm were written by the same author, so shared assumptions are likely.
- The "affected services" truth depends on a 5 % threshold.
- Latency was measured on one developer machine.

## Reproduce

```bash
cd backend
python -m app.bench.runner --seed 42 --scenarios 40 --out ../benchmarks/results
```

The Evaluation Lab UI runs the same code (up to 60 scenarios) and stores results in `evaluation_runs` and `evaluation_results`.
