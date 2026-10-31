"""Aggregations for the overview dashboard, service health, graph and incident charts."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.analysis.anomaly import build_series, events_frame
from app.analysis.graph import build_graph
from app.analysis.types import AnalysisConfig, Event
from app.models import AnalysisRun, Anomaly, Hypothesis, Incident, Project, Service, TelemetryEvent
from app.services.events import load_events

RANGES = {"1h": 1, "6h": 6, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def _bounds(db: Session, project_id: uuid.UUID, range_key: str) -> tuple[datetime | None, datetime | None]:
    first, last = db.execute(select(func.min(TelemetryEvent.occurred_at), func.max(TelemetryEvent.occurred_at))
                             .where(TelemetryEvent.project_id == project_id)).one()
    if range_key == "all" or range_key not in RANGES:
        return first, last
    end = datetime.now(UTC)
    return end - timedelta(hours=RANGES[range_key]), end


def _float(value) -> float | None:
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else round(float(value), 4)


def time_series(events: list[Event], start: datetime, end: datetime, points: int = 60, top_services: int = 8) -> dict:
    span = max((end - start).total_seconds(), 60.0)
    bucket = max(60, int(span / points) // 60 * 60)
    cfg = AnalysisConfig(bucket_seconds=bucket, min_requests_per_bucket=1, min_error_count=1)
    df = events_frame([e for e in events if e.event_type in ("span", "log")], cfg)
    if df.empty:
        return {"bucket_seconds": bucket, "overall": [], "services": {}}
    spans = df[df["event_type"] == "span"]
    base = spans if len(spans) else df
    grouped = base.groupby("bucket")
    overall = pd.DataFrame({
        "requests": grouped.size(),
        "errors": grouped["is_error"].sum(),
        "p95": spans.groupby("bucket")["duration_ms"].quantile(0.95) if len(spans) else np.nan,
    })
    overall_points = [
        {"t": t.isoformat(), "requests": int(r.requests), "error_rate": _float(r.errors / r.requests if r.requests else None),
         "latency_p95_ms": _float(r.p95)}
        for t, r in overall.sort_index().iterrows()
    ]
    series = build_series(df, cfg)
    traffic = Counter(df["service"])
    services = {}
    for service, _ in traffic.most_common(top_services):
        rates = series.get((service, "error_rate"))
        lat = series.get((service, "latency_p95_ms"))
        index = rates.index if rates is not None else []
        services[service] = [
            {"t": t.isoformat(), "error_rate": _float(rates.get(t)) if rates is not None else None,
             "latency_p95_ms": _float(lat.get(t)) if lat is not None else None}
            for t in index
        ]
    return {"bucket_seconds": bucket, "overall": overall_points, "services": services}


def service_health(db: Session, project_id: uuid.UUID, events: list[Event], environment: str | None) -> list[dict]:
    open_incidents = list(db.scalars(select(Incident).where(Incident.project_id == project_id, Incident.status != "resolved")))
    by_service: dict[str, dict] = {}
    for e in events:
        if e.event_type not in ("span", "log"):
            continue
        s = by_service.setdefault(e.service, {"spans": 0, "span_errors": 0, "logs": 0, "log_errors": 0, "durations": []})
        if e.event_type == "span":
            s["spans"] += 1
            s["span_errors"] += e.is_error
            if e.duration_ms is not None:
                s["durations"].append(e.duration_ms)
        else:
            s["logs"] += 1
            s["log_errors"] += e.is_error
    query = select(Service).where(Service.project_id == project_id)
    if environment:
        query = query.where(Service.environment == environment)
    health = []
    for svc in db.scalars(query.order_by(Service.name)):
        s = by_service.get(svc.name)
        requests = (s["spans"] or s["logs"]) if s else 0
        errors = (s["span_errors"] if s and s["spans"] else s["log_errors"] if s else 0)
        rate = errors / requests if requests else None
        p95 = float(np.percentile(s["durations"], 95)) if s and s["durations"] else None
        incidents = [i for i in open_incidents if svc.name in i.affected_services and i.environment == svc.environment]
        status = "no_data" if not requests else "critical" if (rate or 0) >= 0.2 else "degraded" if (rate or 0) >= 0.05 or incidents else "healthy"
        health.append({"service": svc.name, "environment": svc.environment, "kind": svc.kind, "requests": requests,
                       "error_rate": _float(rate), "latency_p95_ms": _float(p95), "open_incidents": len(incidents),
                       "status": status})
    return health


def overview(db: Session, project: Project, environment: str | None, range_key: str) -> dict:
    start, end = _bounds(db, project.id, range_key)
    data_first, data_last = db.execute(select(func.min(TelemetryEvent.occurred_at), func.max(TelemetryEvent.occurred_at))
                                       .where(TelemetryEvent.project_id == project.id)).one()
    events = load_events(db, project.id, environment, start, end) if start else []
    incident_q = select(Incident).where(Incident.project_id == project.id)
    if environment:
        incident_q = incident_q.where(Incident.environment == environment)
    incidents = list(db.scalars(incident_q))
    active = [i for i in incidents if i.status != "resolved"]
    in_range = [i for i in incidents if start and start <= i.started_at <= end]
    anomaly_q = (select(Anomaly).options(selectinload(Anomaly.service)).where(Anomaly.project_id == project.id)
                 .order_by(Anomaly.window_start.desc()).limit(15))
    if start:
        anomaly_q = anomaly_q.where(Anomaly.window_start >= start)
    runs = db.execute(
        select(AnalysisRun, Incident.title).join(Incident, Incident.id == AnalysisRun.incident_id)
        .where(Incident.project_id == project.id).order_by(AnalysisRun.created_at.desc()).limit(8)).all()
    leads = {}
    for run, _ in runs:
        top = db.scalar(select(Hypothesis).where(Hypothesis.analysis_run_id == run.id, Hypothesis.rank == 1))
        leads[run.id] = top.service_name if top else None
    deployments = sorted((e for e in events if e.event_type == "deployment"), key=lambda e: e.occurred_at, reverse=True)[:10]
    return {
        "project": {"id": str(project.id), "name": project.name, "is_demo": project.is_demo},
        "range": {"key": range_key, "start": start, "end": end, "data_first": data_first, "data_last": data_last},
        "stats": {
            "active_incidents": len(active),
            "incidents_in_range": len(in_range),
            "events_in_range": len(events),
            "services": db.scalar(select(func.count(Service.id)).where(Service.project_id == project.id)) or 0,
            "anomalies_in_range": db.scalar(select(func.count(Anomaly.id)).where(
                Anomaly.project_id == project.id, *( [Anomaly.window_start >= start] if start else []))) or 0,
        },
        "severity_distribution": {sev: sum(1 for i in active if i.severity == sev) for sev in ("critical", "high", "medium", "low")},
        "series": time_series(events, start, end) if start and events else {"bucket_seconds": 60, "overall": [], "services": {}},
        "service_health": service_health(db, project.id, events, environment),
        "recent_deployments": [
            {"event_id": e.id, "service": e.service, "occurred_at": e.occurred_at,
             "version": e.attributes.get("version"), "message": e.message}
            for e in deployments
        ],
        "recent_investigations": [
            {"analysis_run_id": str(run.id), "incident_id": str(run.incident_id), "incident_title": title,
             "status": run.status, "created_at": run.created_at, "leading_hypothesis": leads.get(run.id),
             "writer": (run.summary or {}).get("meta", {}).get("writer")}
            for run, title in runs
        ],
        "anomaly_feed": [
            {"id": str(a.id), "service": a.service.name, "anomaly_type": a.anomaly_type, "metric": a.metric,
             "observed": a.observed, "baseline": a.baseline, "score": a.score, "severity": a.severity,
             "window_start": a.window_start, "incident_id": str(a.incident_id) if a.incident_id else None}
            for a in db.scalars(anomaly_q)
        ],
    }


def service_graph(db: Session, project_id: uuid.UUID, environment: str | None, incident: Incident | None) -> dict:
    if incident is not None:
        environment = incident.environment
        start = incident.started_at - timedelta(minutes=30)
        end = (incident.ended_at or incident.started_at) + timedelta(minutes=10)
        events = load_events(db, project_id, environment, start, end)
    else:
        events = load_events(db, project_id, environment, limit=200_000)
    graph = build_graph(events)
    health = {h["service"]: h for h in service_health(db, project_id, events, environment)}
    depth = graph.depth_order()
    affected: set[str] = set(incident.affected_services) if incident else set()
    leading = None
    if incident and incident.latest_analysis_run_id:
        top = db.scalar(select(Hypothesis).where(Hypothesis.analysis_run_id == incident.latest_analysis_run_id, Hypothesis.rank == 1))
        leading = top.service_name if top else None
    blast = graph.transitive_callers(leading) if leading else set()
    nodes = []
    for name in sorted(graph.nodes):
        h = health.get(name, {})
        nodes.append({
            "id": name, "depth": depth.get(name, 0), "kind": h.get("kind", "external"),
            "requests": h.get("requests", 0), "error_rate": h.get("error_rate"), "latency_p95_ms": h.get("latency_p95_ms"),
            "status": h.get("status", "no_data"), "open_incidents": h.get("open_incidents", 0),
            "affected": name in affected, "leading_hypothesis": name == leading, "in_blast_radius": name in blast,
            "callers": sorted(graph.callers(name)), "callees": sorted(graph.callees(name)),
        })
    edges = [
        {"id": f"{e.source}->{e.target}", "source": e.source, "target": e.target, "call_count": e.call_count,
         "error_count": e.error_count, "error_rate": round(e.error_count / e.call_count, 4) if e.call_count else None,
         "latency_p95_ms": _float(e.p95_ms), "origins": sorted(e.origins), "evidence_event_ids": e.evidence_event_ids}
        for e in graph.edges.values()
    ]
    return {"environment": environment, "incident_id": str(incident.id) if incident else None, "leading_service": leading,
            "nodes": nodes, "edges": edges, "events_considered": len(events)}


def incident_metrics(db: Session, incident: Incident) -> dict:
    start = incident.started_at - timedelta(minutes=30)
    end = (incident.ended_at or incident.started_at) + timedelta(minutes=15)
    events = load_events(db, incident.project_id, incident.environment, start, end)
    affected = [e for e in events if e.service in set(incident.affected_services)]
    data = time_series(affected, start, end, points=int((end - start).total_seconds() // 60) or 1, top_services=10)
    data["window"] = {"start": start, "end": end, "incident_start": incident.started_at, "incident_end": incident.ended_at}
    data["deployments"] = [{"event_id": e.id, "service": e.service, "occurred_at": e.occurred_at,
                            "version": e.attributes.get("version")} for e in events if e.event_type == "deployment"]
    return data
