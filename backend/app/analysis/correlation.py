"""Temporal + topological correlation of anomalies into candidate incidents.

Two anomalies belong to the same incident when their windows are close in time AND their
services are related: same service, within ``correlation_max_hops`` in the dependency
graph, or they share trace IDs among supporting events. With no topology at all
(no spans, no explicit dependencies) the grouping degrades to time-only and says so.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime, timedelta

from app.analysis.anomaly import message_signature
from app.analysis.graph import ServiceGraph
from app.analysis.types import AnalysisConfig, Anomaly, CandidateIncident, Event, TimelineEntry

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
TYPE_LABEL = {
    "error_rate_spike": "error spike",
    "latency_spike": "latency regression",
    "traffic_drop": "traffic drop",
    "traffic_surge": "traffic surge",
    "metric_shift": "metric shift",
    "new_error_signature": "new error signature",
}


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _traces_for(anomaly: Anomaly, events_by_id: dict[str, Event]) -> set[str]:
    return {
        events_by_id[eid].trace_id  # type: ignore[misc]
        for eid in anomaly.supporting_event_ids
        if eid in events_by_id and events_by_id[eid].trace_id
    }


def _related(a: Anomaly, b: Anomaly, graph: ServiceGraph, traces: dict[str, set[str]], cfg: AnalysisConfig) -> bool:
    gap = timedelta(minutes=cfg.correlation_gap_minutes)
    if b.window_start > a.window_end + gap or a.window_start > b.window_end + gap:
        return False
    if a.service == b.service:
        return True
    if not graph.edges:
        return True  # no topology available: time proximity only
    if graph.undirected_distance(a.service, b.service, cfg.correlation_max_hops) is not None:
        return True
    return bool(traces[a.id] & traces[b.id])


def incident_title(anomalies: list[Anomaly]) -> str:
    by_service: dict[str, Anomaly] = {}
    for anomaly in sorted(anomalies, key=lambda a: a.window_start):
        by_service.setdefault(anomaly.service, anomaly)
    # Name the incident after the earliest metric-level symptom; error signatures in callers
    # ("upstream X failed") make poor titles.
    metric_level = [a for a in sorted(anomalies, key=lambda a: (a.window_start, -a.score)) if a.anomaly_type != "new_error_signature"]
    first = metric_level[0] if metric_level else next(iter(by_service.values()))
    label = TYPE_LABEL.get(first.anomaly_type, first.anomaly_type)
    others = len(by_service) - 1
    suffix = f" affecting {others} related service{'s' if others != 1 else ''}" if others else ""
    return f"{label.capitalize()} in {first.service}{suffix}"


def build_timeline(
    anomalies: list[Anomaly], events: list[Event], services: set[str], start: datetime, end: datetime, deploy_ids: list[str]
) -> list[TimelineEntry]:
    by_id = {e.id: e for e in events}
    entries: list[TimelineEntry] = []
    for eid in deploy_ids:
        e = by_id[eid]
        version = e.attributes.get("version") or e.attributes.get("change") or ""
        entries.append(TimelineEntry(e.occurred_at, "deployment", e.service, f"Deployment {version}".strip(), event_id=eid))
    for a in anomalies:
        label = TYPE_LABEL.get(a.anomaly_type, a.anomaly_type)
        entries.append(
            TimelineEntry(
                a.window_start, "anomaly_start", a.service,
                f"{label} on {a.metric}: {a.observed:g} vs baseline {a.baseline:g}",
                event_id=a.supporting_event_ids[0] if a.supporting_event_ids else None, anomaly_id=a.id,
            )
        )
        entries.append(TimelineEntry(a.window_end, "anomaly_end", a.service, f"{label} on {a.metric} subsided", anomaly_id=a.id))
    seen_first_error: set[str] = set()
    seen_signature: set[tuple[str, str]] = set()
    # Deployments are looked up over the full lookback window, but error entries start shortly
    # before the first anomaly so routine background failures don't crowd the timeline.
    error_start = min(a.window_start for a in anomalies) - timedelta(minutes=2) if anomalies else start
    for e in sorted(events, key=lambda x: x.occurred_at):
        if not (error_start <= e.occurred_at <= end) or e.service not in services or not e.is_error:
            continue
        if e.service not in seen_first_error:
            seen_first_error.add(e.service)
            entries.append(TimelineEntry(e.occurred_at, "first_error", e.service, f"First error: {(e.message or e.event_type)[:140]}", event_id=e.id))
        elif e.message:
            key = (e.service, message_signature(e.message))
            if key not in seen_signature and len(seen_signature) < 25:
                seen_signature.add(key)
                entries.append(TimelineEntry(e.occurred_at, "error_signature", e.service, e.message[:140], event_id=e.id))
        if e.message:
            seen_signature.add((e.service, message_signature(e.message)))
    entries.sort(key=lambda t: (t.at, t.kind != "deployment"))
    return entries


def correlate(
    anomalies: list[Anomaly], events: Iterable[Event], graph: ServiceGraph, cfg: AnalysisConfig | None = None
) -> list[CandidateIncident]:
    cfg = cfg or AnalysisConfig()
    events = list(events)
    if not anomalies:
        return []
    events_by_id = {e.id: e for e in events}
    traces = {a.id: _traces_for(a, events_by_id) for a in anomalies}
    ordered = sorted(anomalies, key=lambda a: a.window_start)
    uf = _UnionFind(len(ordered))
    for i, a in enumerate(ordered):
        for j in range(i + 1, len(ordered)):
            b = ordered[j]
            if b.window_start > a.window_end + timedelta(minutes=cfg.correlation_gap_minutes):
                break  # sorted by start: no later anomaly can be close to ``a`` either
            if _related(a, b, graph, traces, cfg):
                uf.union(i, j)

    groups: dict[int, list[Anomaly]] = {}
    for i, a in enumerate(ordered):
        groups.setdefault(uf.find(i), []).append(a)

    deployments = [e for e in events if e.event_type == "deployment"]
    lookback = timedelta(minutes=cfg.deployment_lookback_minutes)
    incidents = []
    for members in groups.values():
        services = {a.service for a in members}
        start = min(a.window_start for a in members)
        end = max(a.window_end for a in members)
        neighbours = set(services)
        for s in services:
            neighbours |= graph.callees(s) | graph.callers(s)
        deploy_ids = [
            d.id for d in sorted(deployments, key=lambda d: d.occurred_at)
            if d.service in neighbours and start - lookback <= d.occurred_at <= end
        ]
        severity = max((a.severity for a in members), key=SEVERITY_ORDER.__getitem__)
        fp_raw = f"{start.isoformat()}|{','.join(sorted(services))}"
        incidents.append(
            CandidateIncident(
                fingerprint=hashlib.sha1(fp_raw.encode(), usedforsecurity=False).hexdigest()[:24],
                title=incident_title(members),
                severity=severity,
                started_at=start,
                ended_at=end,
                services=sorted(services, key=lambda s: min(a.window_start for a in members if a.service == s)),
                anomalies=members,
                deployment_event_ids=deploy_ids,
                timeline=build_timeline(members, events, services, start - lookback, end, deploy_ids),
            )
        )
    incidents.sort(key=lambda c: c.started_at)
    return incidents
