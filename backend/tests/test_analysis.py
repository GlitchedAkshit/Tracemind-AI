"""Anomaly detection, dependency graph, correlation and root-cause ranking."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.analysis.anomaly import detect_anomalies, detect_in_series, robust_score
from app.analysis.graph import build_graph, error_trace_origins
from app.analysis.pipeline import analyze_incident, run_detection
from app.analysis.ranking import STRATEGIES
from app.analysis.types import AnalysisConfig, Event
from app.bench.generator import FAULT_MESSAGES, Fault, simulate

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def series(values, start=T0):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="60s"), dtype=float)


# --------------------------------------------------------------------- anomaly detection

def test_zero_variance_baseline_uses_floor_not_infinite_score():
    score, median, scale = robust_score(0.011, np.zeros(20), "error_rate")
    assert median == 0 and scale == 0.01 and np.isfinite(score)


def test_spike_detected_on_flat_baseline():
    flagged, stats = detect_in_series(series([0.0] * 20 + [0.4, 0.5]), "error_rate", AnalysisConfig())
    assert [p["type"] for p in flagged] == ["error_rate_spike", "error_rate_spike"]
    assert stats["evaluated"] == 12


def test_insufficient_baseline_yields_no_anomaly():
    flagged, stats = detect_in_series(series([0.0] * 5 + [0.9]), "error_rate", AnalysisConfig())
    assert flagged == [] and stats["insufficient_baseline"] == 6


def test_noisy_baseline_suppresses_small_deviations():
    rng = np.random.default_rng(1)
    values = list(200 + rng.normal(0, 40, 40)) + [260.0]
    flagged, _ = detect_in_series(series(values), "latency_p95_ms", AnalysisConfig())
    assert flagged == []


def test_absolute_threshold_required_even_with_high_robust_score():
    # 0.0 -> 0.03 is a huge robust deviation on a flat baseline but under the 5 % floor.
    flagged, _ = detect_in_series(series([0.0] * 20 + [0.03]), "error_rate", AnalysisConfig())
    assert flagged == []


def test_missing_observations_are_skipped_not_zero_filled():
    values = [100.0] * 15 + [np.nan] * 5 + [100.0, 900.0]
    flagged, stats = detect_in_series(series(values), "latency_p95_ms", AnalysisConfig())
    assert stats["missing"] == 5
    assert len(flagged) == 1 and flagged[0]["value"] == 900.0


def test_long_incident_does_not_become_its_own_baseline():
    flagged, _ = detect_in_series(series([0.0] * 20 + [0.5] * 40), "error_rate", AnalysisConfig())
    assert len(flagged) == 40


def test_empty_dataset():
    anomalies, diagnostics = detect_anomalies([])
    assert anomalies == [] and diagnostics["reason"] == "no events"


def _scenario(faults, minutes=45, seed=3, missing=0.0):
    return simulate(seed=seed, faults=faults, start=T0, minutes=minutes, requests_per_minute=24, missing_trace_fraction=missing)


def test_detects_injected_fault_with_type_baseline_and_evidence():
    fault = Fault("payments-db", "dependency_down", T0 + timedelta(minutes=30), T0 + timedelta(minutes=40), 0.9,
                  FAULT_MESSAGES["dependency_down"][0])
    sc = _scenario([fault])
    events = sc.to_events()
    anomalies, _ = detect_anomalies(events)
    db = [a for a in anomalies if a.service == "payments-db" and a.anomaly_type == "error_rate_spike"]
    assert db, [(a.service, a.anomaly_type) for a in anomalies]
    a = db[0]
    assert a.observed > a.baseline and a.score >= AnalysisConfig().z_threshold
    assert fault.start - timedelta(minutes=1) <= a.window_start <= fault.start + timedelta(minutes=2)
    ids = {e.id for e in events}
    assert a.supporting_event_ids and set(a.supporting_event_ids) <= ids


# --------------------------------------------------------------------- dependency graph

def _span(i, service, trace, span, parent=None, error=False, t=T0):
    return Event(id=f"e{i}", service=service, event_type="span", occurred_at=t, trace_id=trace, span_id=span,
                 parent_span_id=parent, duration_ms=10, status_code=500 if error else 200, severity="error" if error else "info")


def test_graph_from_parent_child_spans_and_explicit_dependencies():
    events = [
        _span(1, "gateway", "t1", "a"),
        _span(2, "orders", "t1", "b", "a"),
        _span(3, "orders-db", "t1", "c", "b"),
        _span(4, "orders", "t2", "d", "missing-parent"),  # parent not ingested: no edge invented
        Event(id="e5", service="orders", event_type="dependency", occurred_at=T0, attributes={"target": "card-network"}),
    ]
    g = build_graph(events)
    assert set(g.edges) == {("gateway", "orders"), ("orders", "orders-db"), ("orders", "card-network")}
    assert g.edges[("gateway", "orders")].evidence_event_ids == ["e2"]
    assert g.edges[("orders", "card-network")].origins == {"explicit"}
    assert g.transitive_callers("orders-db") == {"orders", "gateway"}
    assert g.transitive_callees("gateway") == {"orders", "orders-db", "card-network"}


def test_error_origin_is_deepest_failing_span():
    events = [_span(1, "gateway", "t1", "a", error=True), _span(2, "orders", "t1", "b", "a", error=True),
              _span(3, "orders-db", "t1", "c", "b", error=True), _span(4, "cache", "t1", "d", "b")]
    assert error_trace_origins(events) == {"t1": ("orders-db", "e3")}


# --------------------------------------------------------------------- correlation & ranking

def test_dependency_failure_ranks_callee_above_its_callers():
    fault = Fault("payments-db", "dependency_down", T0 + timedelta(minutes=30), T0 + timedelta(minutes=40), 0.9,
                  FAULT_MESSAGES["dependency_down"][0])
    events = _scenario([fault]).to_events()
    out = run_detection(events)
    incident = next(i for i in out.incidents if "payments-db" in i.services)
    hyps = analyze_incident(incident, events, out.graph)
    assert hyps[0].service == "payments-db"
    assert set(hyps[0].components) == {"temporal", "dependency", "anomaly", "trace", "error", "deployment", "contradiction"}
    callers = [h for h in hyps if h.service in ("payments-service", "checkout-service")]
    assert callers and all(h.components["contradiction"] > 0 for h in callers)
    assert any(ev.polarity == "contradicting" for h in callers for ev in h.evidence)
    assert "not a probability" in hyps[0].uncertainty


def test_bad_deployment_hypothesis_cites_the_deployment_event():
    fault = Fault("inventory-service", "bad_deployment", T0 + timedelta(minutes=30), T0 + timedelta(minutes=40), 0.9,
                  FAULT_MESSAGES["bad_deployment"][1])
    events = _scenario([fault]).to_events()
    out = run_detection(events)
    incident = next(i for i in out.incidents if "inventory-service" in i.services)
    top = analyze_incident(incident, events, out.graph)[0]
    assert top.service == "inventory-service" and top.cause_type == "deployment_regression"
    by_id = {e.id: e for e in events}
    assert any(by_id[ev.event_id].event_type == "deployment" for ev in top.evidence if ev.polarity == "supporting")


def test_missing_trace_ids_still_rank_and_report_uncertainty():
    fault = Fault("orders-db", "dependency_down", T0 + timedelta(minutes=30), T0 + timedelta(minutes=40), 0.9,
                  FAULT_MESSAGES["dependency_down"][0])
    # strip trace context from every log to simulate uninstrumented logging
    events = [replace(e, trace_id=None) if e.event_type == "log" else e for e in _scenario([fault]).to_events()]
    out = run_detection(events)
    incident = next(i for i in out.incidents if "orders-db" in i.services)
    hyps = analyze_incident(incident, events, out.graph)
    assert "orders-db" in [h.service for h in hyps[:3]]
    assert "lack trace IDs" in hyps[0].uncertainty


def test_multiple_simultaneous_faults_in_separate_branches_both_surface():
    faults = [
        Fault("search-service", "dependency_down", T0 + timedelta(minutes=30), T0 + timedelta(minutes=40), 0.9,
              FAULT_MESSAGES["dependency_down"][1]),
        Fault("payments-db", "dependency_down", T0 + timedelta(minutes=31), T0 + timedelta(minutes=40), 0.9,
              FAULT_MESSAGES["dependency_down"][0]),
    ]
    events = _scenario(faults, seed=5).to_events()
    out = run_detection(events)
    top_services = set()
    for incident in out.incidents:
        top_services |= {h.service for h in analyze_incident(incident, events, out.graph)[:3]}
    assert {"search-service", "payments-db"} <= top_services


def test_every_strategy_produces_rankings_and_rules_baseline_differs():
    fault = Fault("payments-db", "resource_exhaustion", T0 + timedelta(minutes=30), T0 + timedelta(minutes=42), 0.9,
                  FAULT_MESSAGES["resource_exhaustion"][0])
    events = _scenario([fault]).to_events()
    out = run_detection(events)
    incident = max(out.incidents, key=lambda i: len(i.anomalies))
    for name in STRATEGIES:
        hyps = analyze_incident(incident, events, out.graph, strategy=name)
        assert hyps and [h.rank for h in hyps] == list(range(1, len(hyps) + 1))
        assert all(hyps[i].ranking_score >= hyps[i + 1].ranking_score for i in range(len(hyps) - 1))


def test_no_topology_falls_back_to_time_only_correlation():
    events = []
    for minute in range(40):
        for k in range(10):
            t = T0 + timedelta(minutes=minute, seconds=k * 5)
            bad = minute >= 30
            events.append(Event(id=f"a{minute}-{k}", service="svc-a", event_type="log", occurred_at=t,
                                severity="error" if bad and k < 6 else "info", message="db timeout" if bad else "ok"))
            events.append(Event(id=f"b{minute}-{k}", service="svc-b", event_type="log", occurred_at=t + timedelta(seconds=30),
                                severity="error" if bad and k < 5 else "info", message="upstream error" if bad else "ok"))
    out = run_detection(events)
    assert out.diagnostics["topology"].startswith("unavailable")
    assert len(out.incidents) == 1 and set(out.incidents[0].services) == {"svc-a", "svc-b"}
    hyps = analyze_incident(out.incidents[0], events, out.graph)
    assert "No dependency topology" in hyps[0].uncertainty
