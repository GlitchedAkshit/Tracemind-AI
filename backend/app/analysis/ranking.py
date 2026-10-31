"""Interpretable, weighted root-cause ranking.

Every candidate service receives component scores in [0, 1]:

* ``temporal``      – how early its first anomaly (or deployment) is relative to incident onset
* ``dependency``    – share of the other anomalous services that transitively call it,
                      i.e. how much of the incident its failure could explain
* ``anomaly``       – strength of its strongest anomaly
* ``trace``         – share of failing traces whose deepest failing span is in this service
* ``error``         – new error signatures / error-rate spikes on the service itself
* ``deployment``    – a deployment of the service shortly before onset
* ``contradiction`` – evidence it is a *victim*: a callee failed first, its failing traces
                      originate in a callee, or its deployment happened after onset

``ranking_score = Σ wᵢ·componentᵢ − w_contradiction·contradiction``.  The result is a
relative ranking score, **not** a probability; it has not been calibrated.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.analysis.anomaly import RESOURCE_HINTS
from app.analysis.graph import ServiceGraph, error_trace_origins
from app.analysis.types import AnalysisConfig, Anomaly, CandidateIncident, Event, EvidenceRef, RankedHypothesis

COMPONENTS = ("temporal", "dependency", "anomaly", "trace", "error", "deployment")


@dataclass(frozen=True, slots=True)
class Strategy:
    name: str
    weights: dict[str, float]
    contradiction_weight: float
    description: str
    rules_only: bool = False


STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in [
        Strategy(
            "rules_only",
            {},
            0.0,
            "Baseline rules: blame the most recently deployed anomalous service, otherwise the loudest anomaly.",
            rules_only=True,
        ),
        Strategy("temporal", {"temporal": 0.8, "anomaly": 0.2}, 0.0, "Earliest anomaly onset wins."),
        Strategy(
            "dependency_aware",
            {"temporal": 0.35, "dependency": 0.35, "anomaly": 0.3},
            0.35,
            "Temporal precedence plus dependency relevance and victim contradictions.",
        ),
        Strategy(
            "hybrid",
            {"temporal": 0.22, "dependency": 0.2, "anomaly": 0.1, "trace": 0.22, "error": 0.12, "deployment": 0.14},
            0.35,
            "Full pipeline: temporal, dependency, anomaly strength, trace origins, error and deployment evidence.",
        ),
        Strategy(
            "hybrid_no_traces",
            {"temporal": 0.3, "dependency": 0.25, "anomaly": 0.12, "error": 0.15, "deployment": 0.18},
            0.35,
            "Ablation: hybrid without trace-origin evidence.",
        ),
        Strategy(
            "hybrid_no_deployments",
            {"temporal": 0.26, "dependency": 0.24, "anomaly": 0.12, "trace": 0.24, "error": 0.14},
            0.35,
            "Ablation: hybrid without deployment evidence.",
        ),
        Strategy(
            "hybrid_no_contradictions",
            {"temporal": 0.22, "dependency": 0.2, "anomaly": 0.1, "trace": 0.22, "error": 0.12, "deployment": 0.14},
            0.0,
            "Ablation: hybrid without the contradiction penalty.",
        ),
    ]
}
DEFAULT_STRATEGY = "hybrid"

CAUSE_LABEL = {
    "deployment_regression": "Regression introduced by a recent deployment",
    "resource_saturation": "Resource saturation",
    "latency_degradation": "Latency degradation",
    "error_spike": "Elevated failure rate",
    "traffic_anomaly": "Abnormal traffic volume",
}

SUGGESTED_CHECKS = {
    "deployment_regression": [
        "Diff the configuration and code between {version} and the previous release of {service}.",
        "Check whether failing requests are limited to instances running {version}.",
        "Review {service} release notes and feature flags toggled in the same window.",
    ],
    "resource_saturation": [
        "Inspect {service} resource metrics (CPU, memory, connection pools) around {onset}.",
        "Compare current pool/limit configuration against observed concurrency.",
        "Look for slow queries or lock contention in {service} during the incident window.",
    ],
    "latency_degradation": [
        "Break down {service} span durations by operation to find the slowest path.",
        "Check {service}'s own dependencies for timeouts or retries starting near {onset}.",
        "Compare instance-level latency to rule out a single unhealthy host.",
    ],
    "error_spike": [
        "Group {service} errors by message and status code starting at {onset}.",
        "Inspect the first failing traces through {service} end to end.",
        "Verify recent changes to {service} configuration, secrets, or upstream contracts.",
    ],
    "traffic_anomaly": [
        "Confirm whether the traffic change at {onset} matches a known client release or campaign.",
        "Check load balancer and gateway logs for {service} for dropped or rejected requests.",
    ],
}


@dataclass(slots=True)
class _Candidate:
    service: str
    anomalies: list[Anomaly]
    onset: datetime | None
    deployment: Event | None
    late_deployment: Event | None


def _deployment_version(event: Event | None) -> str:
    if event is None:
        return "the latest release"
    return str(event.attributes.get("version") or event.attributes.get("change") or "the latest release")


def _cause_type(c: _Candidate, deployment_score: float) -> str:
    if deployment_score >= 0.3:
        return "deployment_regression"
    types = {a.anomaly_type for a in c.anomalies}
    shifts = [a for a in c.anomalies if a.anomaly_type == "metric_shift"]
    if any(a.details.get("resource_metric") or any(h in a.metric.lower() for h in RESOURCE_HINTS) for a in shifts):
        return "resource_saturation"
    if "latency_spike" in types and not types & {"error_rate_spike", "new_error_signature"}:
        return "latency_degradation"
    if types & {"error_rate_spike", "new_error_signature"}:
        return "error_spike"
    if types & {"traffic_drop", "traffic_surge"}:
        return "traffic_anomaly"
    return "latency_degradation" if "latency_spike" in types else "error_spike"


def rank_hypotheses(
    incident: CandidateIncident,
    events: list[Event],
    graph: ServiceGraph,
    cfg: AnalysisConfig | None = None,
    strategy: str = DEFAULT_STRATEGY,
    top_k: int = 5,
) -> list[RankedHypothesis]:
    cfg = cfg or AnalysisConfig()
    strat = STRATEGIES[strategy]
    by_id = {e.id: e for e in events}
    lookback = timedelta(minutes=cfg.deployment_lookback_minutes)
    t0 = incident.started_at

    # ---- candidates -------------------------------------------------------------------
    anomalies_by_service: dict[str, list[Anomaly]] = defaultdict(list)
    for a in incident.anomalies:
        anomalies_by_service[a.service].append(a)
    deployments = sorted((by_id[i] for i in incident.deployment_event_ids if i in by_id), key=lambda e: e.occurred_at)
    candidates: dict[str, _Candidate] = {}
    for service in set(anomalies_by_service) | {d.service for d in deployments}:
        own = anomalies_by_service.get(service, [])
        onset = min((a.window_start for a in own), default=None)
        reference = onset or t0
        before = [d for d in deployments if d.service == service and reference - lookback <= d.occurred_at <= reference + cfg.bucket]
        after = [d for d in deployments if d.service == service and d.occurred_at > reference + cfg.bucket]
        candidates[service] = _Candidate(service, own, onset, before[-1] if before else None, after[0] if after else None)

    anomalous = set(anomalies_by_service)
    window_start, window_end = t0 - lookback, incident.ended_at
    scoped = [e for e in events if window_start <= e.occurred_at <= window_end]
    origins = error_trace_origins(e for e in scoped if e.service in anomalous or e.event_type == "span")
    origin_counts = Counter(service for service, _ in origins.values())
    origin_examples: dict[str, list[str]] = defaultdict(list)
    for _trace_id, (service, span_id) in sorted(origins.items()):
        origin_examples[service].append(span_id)
    traces_by_service: dict[str, set[str]] = defaultdict(set)
    for e in scoped:
        if e.event_type == "span" and e.trace_id in origins and e.is_error:
            traces_by_service[e.service].add(e.trace_id)
    error_events = [e for e in scoped if e.is_error]
    untraced_errors = sum(1 for e in error_events if not e.trace_id)

    incident_span_min = max((incident.ended_at - t0).total_seconds() / 60.0, cfg.bucket_seconds / 60.0)

    results: list[tuple[float, dict[str, float], _Candidate, str, list[str]]] = []
    for c in candidates.values():
        comp: dict[str, float] = {}
        # temporal precedence
        if c.onset is not None:
            lag = max(0.0, (c.onset - t0).total_seconds() / 60.0)
            comp["temporal"] = math.exp(-lag / cfg.temporal_tau_minutes)
        else:
            comp["temporal"] = 0.25 if c.deployment else 0.0
        # dependency relevance
        others = anomalous - {c.service}
        if others:
            explained = others & graph.transitive_callers(c.service)
            comp["dependency"] = len(explained) / len(others)
        else:
            comp["dependency"] = 0.5  # single-service incident: dependency evidence is neutral
        # anomaly strength
        peak = max((a.score for a in c.anomalies), default=0.0)
        comp["anomaly"] = 1.0 - math.exp(-peak / 10.0)
        # trace origins
        total_origins = sum(origin_counts.values())
        comp["trace"] = origin_counts.get(c.service, 0) / total_origins if total_origins else 0.0
        # error evidence
        types = {a.anomaly_type for a in c.anomalies}
        if "new_error_signature" in types:
            comp["error"] = 1.0
        elif "error_rate_spike" in types:
            comp["error"] = 0.6
        elif any(e.service == c.service for e in error_events):
            comp["error"] = 0.25
        else:
            comp["error"] = 0.0
        # deployment
        if c.deployment is not None:
            gap = max(0.0, ((c.onset or t0) - c.deployment.occurred_at).total_seconds() / 60.0)
            comp["deployment"] = math.exp(-gap / (cfg.deployment_lookback_minutes / 2))
        else:
            comp["deployment"] = 0.0
        # contradictions: evidence that this service is a victim, not a cause
        contradiction = 0.0
        earlier_callees = [
            o for o in graph.transitive_callees(c.service) & anomalous
            if c.onset is not None and candidates[o].onset is not None and candidates[o].onset < c.onset  # type: ignore[operator]
        ]
        if earlier_callees:
            contradiction += 0.4
        involved = traces_by_service.get(c.service, set())
        if involved:
            victim_share = sum(1 for t in involved if origins[t][0] != c.service) / len(involved)
            contradiction += 0.4 * victim_share
        if c.late_deployment is not None and c.deployment is None:
            contradiction += 0.2
        if c.onset is None:
            contradiction += 0.2  # deployed but showed no anomaly itself
        comp["contradiction"] = min(1.0, contradiction)

        if strat.rules_only:
            # Classic runbook rule: a deployed anomalous service first, then the loudest alarm.
            score = (1.0 if c.deployment is not None and c.onset is not None else 0.0) + 0.5 * comp["anomaly"]
        else:
            score = sum(strat.weights.get(k, 0.0) * comp[k] for k in COMPONENTS)
            score -= strat.contradiction_weight * comp["contradiction"]
        cause = _cause_type(c, comp["deployment"])
        results.append((round(score, 4), {k: round(v, 4) for k, v in comp.items()}, c, cause, sorted(earlier_callees)))

    results.sort(key=lambda r: (-r[0], r[2].onset or datetime.max.replace(tzinfo=t0.tzinfo), r[2].service))
    hypotheses: list[RankedHypothesis] = []
    for rank, (score, comp, c, cause, earlier_callees) in enumerate(results[:top_k], start=1):
        evidence = _collect_evidence(c, incident, scoped, by_id, origins, origin_examples, earlier_callees, candidates, cfg)
        runner_up = results[rank][0] if rank < len(results) else None
        hypotheses.append(
            RankedHypothesis(
                rank=rank,
                service=c.service,
                cause_type=cause,
                ranking_score=score,
                components=comp,
                summary=_summary(c, cause, comp, t0),
                uncertainty=_uncertainty(c, comp, score, runner_up, origins, error_events, untraced_errors, graph, incident_span_min),
                suggested_checks=[
                    s.format(service=c.service, version=_deployment_version(c.deployment), onset=(c.onset or t0).strftime("%H:%M UTC"))
                    for s in SUGGESTED_CHECKS[cause]
                ],
                evidence=evidence,
            )
        )
    return hypotheses


def _collect_evidence(
    c: _Candidate,
    incident: CandidateIncident,
    scoped: list[Event],
    by_id: dict[str, Event],
    origins: dict[str, tuple[str, str]],
    origin_examples: dict[str, list[str]],
    earlier_callees: list[str],
    candidates: dict[str, _Candidate],
    cfg: AnalysisConfig,
) -> list[EvidenceRef]:
    cap = cfg.max_evidence_per_polarity
    supporting: list[EvidenceRef] = []
    contradicting: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()

    def add(bucket: list[EvidenceRef], event_id: str | None, polarity: str, text: str) -> None:
        if event_id and event_id in by_id and (event_id, polarity) not in seen and len(bucket) < cap:
            seen.add((event_id, polarity))
            bucket.append(EvidenceRef(event_id, polarity, text))

    if c.deployment is not None:
        gap = ((c.onset or incident.started_at) - c.deployment.occurred_at).total_seconds() / 60
        reference = "its first anomaly" if c.onset is not None else "the incident started"
        add(supporting, c.deployment.id, "supporting",
            f"Deployment {_deployment_version(c.deployment)} of {c.service} {gap:.0f} min before {reference}.")
    for a in sorted(c.anomalies, key=lambda x: x.window_start):
        if a.supporting_event_ids:
            label = a.anomaly_type.replace("_", " ")
            add(supporting, a.supporting_event_ids[0], "supporting",
                f"Part of {label} on {a.metric}: observed {a.observed:g} vs baseline {a.baseline:g} (robust score {a.score:.1f}).")
    for span_id in origin_examples.get(c.service, [])[:3]:
        add(supporting, span_id, "supporting", f"Deepest failing span of trace {by_id[span_id].trace_id} is in {c.service}.")
    first_error = next((e for e in sorted(scoped, key=lambda e: e.occurred_at) if e.service == c.service and e.is_error), None)
    if first_error:
        add(supporting, first_error.id, "supporting", f"First error recorded for {c.service} in the incident window.")

    for callee in earlier_callees[:3]:
        other = candidates[callee]
        first = next((a for a in sorted(other.anomalies, key=lambda x: x.window_start) if a.supporting_event_ids), None)
        if first:
            add(contradicting, first.supporting_event_ids[0], "contradicting",
                f"Dependency {callee} (called by {c.service}) became anomalous earlier, at {first.window_start:%H:%M:%S} UTC.")
    victim_traces = [
        (trace, origin) for trace, origin in sorted(origins.items())
        if origin[0] != c.service and any(e.trace_id == trace and e.service == c.service and e.is_error for e in scoped)
    ]
    for trace, (origin_service, span_id) in victim_traces[:3]:
        add(contradicting, span_id, "contradicting", f"In trace {trace} the failure originates in {origin_service}, not {c.service}.")
    if c.late_deployment is not None and c.deployment is None:
        add(contradicting, c.late_deployment.id, "contradicting", "This deployment happened after the incident had already started.")
    if c.onset is None and c.deployment is not None:
        add(contradicting, c.deployment.id, "contradicting", f"{c.service} was deployed but showed no anomaly of its own.")
    return supporting + contradicting


def _summary(c: _Candidate, cause: str, comp: dict[str, float], t0: datetime) -> str:
    parts = [f"{CAUSE_LABEL[cause]} in {c.service}."]
    if c.onset is not None:
        lag = (c.onset - t0).total_seconds() / 60
        parts.append("Its anomalies began at incident onset." if lag < 0.5 else f"Its anomalies began {lag:.0f} min after incident onset.")
    if c.deployment is not None:
        parts.append(f"Deployment {_deployment_version(c.deployment)} preceded the onset.")
    if comp.get("dependency", 0) >= 0.5 and comp.get("dependency") != 0.5:
        parts.append(f"{comp['dependency']:.0%} of the other affected services depend on it.")
    if comp.get("trace", 0) > 0:
        parts.append(f"{comp['trace']:.0%} of failing traces originate here.")
    return " ".join(parts)


def _uncertainty(
    c: _Candidate,
    comp: dict[str, float],
    score: float,
    runner_up: float | None,
    origins: dict,
    error_events: list[Event],
    untraced_errors: int,
    graph: ServiceGraph,
    span_min: float,
) -> str:
    notes = ["Ranking score is relative, not a probability; correlation in time does not prove causation."]
    if runner_up is not None and score - runner_up < 0.05:
        notes.append(f"The next candidate scores within {score - runner_up:.3f}; the ranking is not decisive.")
    if not origins:
        notes.append("No failing traces were available, so trace-origin evidence could not be used.")
    elif error_events and untraced_errors / len(error_events) > 0.3:
        notes.append(f"{untraced_errors / len(error_events):.0%} of error events lack trace IDs.")
    if not graph.edges:
        notes.append("No dependency topology was observed; dependency relevance is unavailable.")
    if c.deployment is None and comp.get("deployment", 0) == 0:
        notes.append("No deployment of this service was recorded near onset; a configuration or external change cannot be ruled out.")
    if comp.get("contradiction", 0) > 0:
        notes.append("Some evidence suggests this service may be reacting to another failure (see contradicting evidence).")
    if span_min < 3:
        notes.append("The incident window is very short; there is little data to compare.")
    return " ".join(notes)
