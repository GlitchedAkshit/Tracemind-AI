"""High-level entry points composing detection, correlation, graph and ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from app.analysis.anomaly import detect_anomalies
from app.analysis.correlation import correlate
from app.analysis.graph import ServiceGraph, build_graph
from app.analysis.ranking import DEFAULT_STRATEGY, rank_hypotheses
from app.analysis.types import AnalysisConfig, Anomaly, CandidateIncident, Event, RankedHypothesis


@dataclass(slots=True)
class DetectionOutput:
    anomalies: list[Anomaly]
    incidents: list[CandidateIncident]
    graph: ServiceGraph
    diagnostics: dict = field(default_factory=dict)


def run_detection(events: list[Event], cfg: AnalysisConfig | None = None) -> DetectionOutput:
    cfg = cfg or AnalysisConfig()
    graph = build_graph(events)
    anomalies, diagnostics = detect_anomalies(events, cfg)
    incidents = correlate(anomalies, events, graph, cfg)
    diagnostics.update(
        events=len(events),
        anomalies=len(anomalies),
        incidents=len(incidents),
        graph_edges=len(graph.edges),
        topology="observed" if graph.edges else "unavailable (time-only correlation)",
    )
    return DetectionOutput(anomalies, incidents, graph, diagnostics)


def incident_scope(incident: CandidateIncident, events: list[Event], cfg: AnalysisConfig) -> list[Event]:
    start = incident.started_at - timedelta(minutes=cfg.deployment_lookback_minutes)
    end = incident.ended_at + timedelta(minutes=5)
    return [e for e in events if start <= e.occurred_at <= end]


def analyze_incident(
    incident: CandidateIncident,
    events: list[Event],
    graph: ServiceGraph | None = None,
    cfg: AnalysisConfig | None = None,
    strategy: str = DEFAULT_STRATEGY,
) -> list[RankedHypothesis]:
    cfg = cfg or AnalysisConfig()
    graph = graph or build_graph(events)
    return rank_hypotheses(incident, incident_scope(incident, events, cfg), graph, cfg, strategy)
