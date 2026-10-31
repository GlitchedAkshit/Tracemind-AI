# Algorithms

Algorithm version `tm-rca-1.0.0`. All modules are pure Python and unit-tested (`backend/tests/test_analysis.py`). Defaults are in `AnalysisConfig` (`backend/app/analysis/types.py`). Projects can override the main thresholds in Settings.

## 1. Time series

Events are bucketed (default 60 s) per service:

| Series | Definition |
|---|---|
| `request_count` | spans per bucket (logs if the service has no spans). Empty buckets inside the service's active range are real zeros |
| `error_rate` | failing / total. **Undefined (missing)** if requests < 3, or if errors < 3 while the rate is ≥ 5 %, so "1 failure out of 6 requests" never reads as a 17 % spike |
| `latency_p95_ms` | p95 span duration, defined only when requests ≥ 3 |
| `metric:<name>` | mean of explicit metric events |

A failure is an event with severity error/critical/fatal, a status code ≥ 500, or `attributes.status = "error"`.

## 2. Anomaly detection (robust baseline)

For bucket *t* with value *x*, the baseline is the last *W* = 30 **non-anomalous**, non-missing observations (minimum 10; otherwise the point counts as `insufficient_baseline`):

```
median = median(baseline)
MAD    = median(|baseline − median|)
scale  = max(1.4826 · MAD, floor(metric, median))
score  = (x − median) / scale          (|score| for request counts and metrics)
```

`floor` prevents zero-variance baselines from producing infinite scores:

| Metric | Floor |
|---|---|
| error rate | 0.01 |
| latency | max(5 ms, 5 % of median) |
| counts | max(1, 10 % of median) |
| metrics | 5 % of median |

A bucket is anomalous only if `score ≥ 4` **and** an absolute, operator-meaningful condition holds:

| Type | Absolute condition |
|---|---|
| `error_rate_spike` | rate ≥ 5 % and ≥ +4 points over the median |
| `latency_spike` | +80 ms and ≥ 1.4 × median |
| `traffic_drop` / `traffic_surge` | ≤ 50 % of the median / ≥ 2.5 × median + 5 |
| `metric_shift` | relative change ≥ 40 % |

Anomalous points are excluded from later baselines, so a long incident does not become its own baseline. Consecutive flagged buckets of the same type, with gaps of up to 2 buckets, merge into one window. The window records baseline, peak observed value, peak score, severity and up to 20 supporting event IDs (failing events, slowest spans, or metric samples).

**New error signatures.** Messages are normalised (UUIDs, hex, long IDs and numbers become placeholders). A signature never seen during the learning period (the first 10 buckets) becomes an anomaly when it occurs ≥ 3 times within 3 minutes. Its onset is the occurrence completing that burst, and the window ends with the burst. This stops a stray background failure from moving the onset early, and stops recurring caller messages from bridging separate incidents.

## 3. Dependency graph

For every span with `parent_span_id`, if the parent span (same trace) belongs to another service, the edge is caller → callee. Each edge records call count, failures, durations (p95) and up to 10 evidence span IDs. `dependency` events add declared edges (`origins = {"explicit"}`). Helpers:

- transitive callers (the *blast radius*: services that depend on X)
- transitive callees
- undirected hop distance
- layered depth for layout

**Error origin per trace.** Among failing spans, the origin is a failing span none of whose children failed (the earliest one if there are several). This is the "deepest failing span".

## 4. Correlation into incidents

Anomalies are sorted by start time. Two anomalies are linked (union-find) when their windows are within 5 minutes **and** at least one of the following holds:

- they are on the same service, or
- the services are ≤ 3 hops apart in the dependency graph, or
- their supporting events share trace IDs.

With no topology at all, linking falls back to time only, and diagnostics report `topology: unavailable`.

Each group becomes a candidate incident with:

- a severity equal to its worst anomaly,
- a title from the earliest metric-level symptom,
- deployments on involved or neighbouring services from 30 minutes before onset to the end, and
- a timeline of deployments, anomaly starts and ends, the first error per service, and new error messages.

## 5. Root-cause ranking

Candidates are every anomalous service plus every service deployed in the window. Each candidate gets components in [0, 1]:

| Component | Definition |
|---|---|
| temporal | exp(−lag / 4 min), where lag = its first anomaly − incident onset. 0.25 if only deployed |
| dependency | share of the *other* anomalous services that transitively call it (0.5 when it is the only service) |
| anomaly | 1 − exp(−peak score / 10) |
| trace | share of failing traces whose origin is this service |
| error | 1.0 new error signature · 0.6 error-rate spike · 0.25 any errors · 0 |
| deployment | exp(−gap / 15 min) for its own deployment ≤ 30 min before onset |
| contradiction | +0.4 if a callee became anomalous earlier · +0.4 × share of its failing traces that originate elsewhere · +0.2 deployed only after onset · +0.2 deployed but no anomaly of its own (capped at 1) |

`ranking_score = Σ wᵢ · componentᵢ − w_c · contradiction`.

Hybrid weights:

| temporal | dependency | anomaly | trace | error | deployment | contradiction penalty |
|---|---|---|---|---|---|---|
| 0.22 | 0.20 | 0.10 | 0.22 | 0.12 | 0.14 | 0.35 |

The score is relative and **uncalibrated**: it is not a probability. Components are stored with every hypothesis and shown in the UI.

**Cause type.** The first rule that matches wins:

1. deployment component ≥ 0.3 → deployment regression
2. resource metric shift (pool / CPU / memory / queue …) → resource saturation
3. latency without errors → latency degradation
4. errors → error spike
5. traffic change → traffic anomaly

**Evidence.**

- Supporting: the deployment event, the first supporting event of each anomaly, origin spans, and the first error.
- Contradicting: earlier anomalies in callees, traces whose failure originates elsewhere, and late or no-effect deployments.

Each list is capped at 8 events. Each event carries a one-sentence explanation.

**Uncertainty text** always states that the score is not a probability and that correlation is not causation. Depending on the case it also notes:

- a runner-up within 0.05,
- missing failing traces, or > 30 % of errors without trace IDs,
- no topology,
- no deployment recorded (so a configuration or external change can't be ruled out),
- the presence of contradicting evidence, and
- very short windows.

**Suggested checks** are templated per cause type and are read-only (diffs, breakdowns, comparisons). Nothing is executed.

## 6. Strategies (baseline and ablations)

| Name | Definition |
|---|---|
| `rules_only` | deployed-and-anomalous service first, then loudest anomaly |
| `temporal` | 0.8 temporal + 0.2 anomaly |
| `dependency_aware` | temporal, dependency and anomaly, with the contradiction penalty |
| `hybrid` | all components |
| `hybrid_no_traces` / `hybrid_no_deployments` / `hybrid_no_contradictions` | ablations |

## 7. LLM investigation writer

1. **Bounded bundle**: the incident, the ranked hypotheses with components, and at most 60 referenced events, each under an alias `E01..En`. Messages are truncated to 300 characters, stripped of control characters and code fences, and only whitelisted attributes are kept. Messages matching prompt-injection patterns are flagged with a warning field.
2. **System prompt**: telemetry is untrusted data; cite only aliases; separate observation from inference; read-only suggestions; say when evidence is insufficient.
3. **Structured output**: JSON schema (strict, `additionalProperties: false`) via the Anthropic Messages API `output_config.format`. The model gets no tools.
4. **Validation**:
   - Pydantic schema check.
   - Every cited alias must exist and is mapped back to a real event ID.
   - Unknown aliases are removed and counted.
   - Hypotheses about services outside the incident are dropped.
   - If more than half of the citations are fabricated, the response is rejected.
   - Suggestions that would change systems are prefixed "[Requires human approval]".
5. **Fallback**: errors, refusals, truncation, schema or citation failures all use the deterministic writer, with the reason recorded in `summary.meta.fallback_reason`.

The assistant uses the same bundle. Before calling the model it adds intent classification (change / start / support / contradict / next / lookup) and keyword retrieval over the bundle. The deterministic answerer covers every intent and returns `insufficient_evidence: true` when the bundle can't answer, for example when the question names a service absent from the incident.
