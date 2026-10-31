"""Project-level anomaly detection and incident grouping, persisted idempotently.

Re-running detection over the same data yields the same anomalies (fingerprinted) and
updates the same incidents (matched by time overlap and shared services) rather than
creating duplicates.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis.correlation import SEVERITY_ORDER
from app.analysis.pipeline import run_detection
from app.analysis.types import AnalysisConfig, CandidateIncident
from app.models import AnalysisRun, Anomaly, Incident, IncidentEvent, Project, Service
from app.services.events import load_events

MAX_SUPPORTING_LINKS = 20


def _matching_incident(db: Session, project_id: uuid.UUID, environment: str, cand: CandidateIncident,
                       gap: timedelta, claimed: set[uuid.UUID]) -> Incident | None:
    rows = db.scalars(select(Incident).where(
        Incident.project_id == project_id, Incident.environment == environment,
        Incident.started_at <= cand.ended_at + gap,
    ))
    for inc in rows:
        end = inc.ended_at or inc.started_at
        if inc.id not in claimed and end >= cand.started_at - gap and set(inc.affected_services) & set(cand.services):
            return inc
    return None


def _link(db: Session, incident_id: uuid.UUID, event_ids: list[str], role: str, existing: set[uuid.UUID]) -> None:
    for raw in event_ids:
        eid = uuid.UUID(raw)
        if eid not in existing:
            existing.add(eid)
            db.add(IncidentEvent(incident_id=incident_id, event_id=eid, role=role))


def detect_project(db: Session, project_id: uuid.UUID, environments: list[str] | None = None) -> dict:
    project = db.get(Project, project_id)
    cfg = AnalysisConfig.from_dict(project.analysis_settings if project else None)
    if environments is None:
        environments = sorted(set(db.scalars(select(Service.environment).where(Service.project_id == project_id))))
    summary: dict = {"environments": {}}
    for env in environments:
        events = load_events(db, project_id, env)
        output = run_detection(events, cfg)
        services = {s.name: s for s in db.scalars(select(Service).where(Service.project_id == project_id, Service.environment == env))}

        current_fps = {a.id for a in output.anomalies}
        stale = db.scalars(select(Anomaly).join(Service, Service.id == Anomaly.service_id).where(
            Anomaly.project_id == project_id, Service.environment == env))
        for row in stale:
            if row.fingerprint not in current_fps:
                db.delete(row)
        db.flush()

        created = updated = 0
        claimed: set[uuid.UUID] = set()
        for cand in output.incidents:
            incident = _matching_incident(db, project_id, env, cand, timedelta(minutes=cfg.correlation_gap_minutes), claimed)
            if incident is None:
                incident = Incident(
                    id=uuid.uuid4(), project_id=project_id, environment=env,
                    fingerprint=hashlib.sha1(f"{env}|{cand.fingerprint}".encode(), usedforsecurity=False).hexdigest()[:24],
                    title=cand.title, severity=cand.severity, started_at=cand.started_at, ended_at=cand.ended_at,
                    affected_services=cand.services, status="open",
                )
                db.add(incident)
                created += 1
            else:
                incident.title = cand.title
                incident.started_at = min(incident.started_at, cand.started_at)
                incident.ended_at = max(incident.ended_at or cand.ended_at, cand.ended_at)
                incident.affected_services = list(dict.fromkeys([*incident.affected_services, *cand.services]))
                if SEVERITY_ORDER[cand.severity] > SEVERITY_ORDER[incident.severity]:
                    incident.severity = cand.severity
                updated += 1
            claimed.add(incident.id)
            incident.timeline = [
                {"at": t.at.isoformat(), "kind": t.kind, "service": t.service, "title": t.title,
                 "event_id": t.event_id, "anomaly_id": t.anomaly_id}
                for t in cand.timeline
            ]
            db.flush()
            linked = set(db.scalars(select(IncidentEvent.event_id).where(IncidentEvent.incident_id == incident.id)))
            for a in cand.anomalies:
                row = db.scalar(select(Anomaly).where(Anomaly.project_id == project_id, Anomaly.fingerprint == a.id))
                if row is None:
                    row = Anomaly(project_id=project_id, fingerprint=a.id, service_id=services[a.service].id)
                    db.add(row)
                row.incident_id = incident.id
                row.anomaly_type, row.metric, row.severity = a.anomaly_type, a.metric, a.severity
                row.baseline, row.observed, row.score = a.baseline, a.observed, a.score
                row.window_start, row.window_end = a.window_start, a.window_end
                row.supporting_event_ids, row.details = a.supporting_event_ids, a.details
                _link(db, incident.id, a.supporting_event_ids[:MAX_SUPPORTING_LINKS], "anomaly", linked)
            _link(db, incident.id, cand.deployment_event_ids, "deployment", linked)
            _link(db, incident.id, [t.event_id for t in cand.timeline if t.event_id], "timeline", linked)
            db.flush()

        # Incidents left without anomalies and never analysed were superseded by regrouping.
        orphans = db.scalars(select(Incident).where(
            Incident.project_id == project_id, Incident.environment == env,
            ~Incident.id.in_(select(Anomaly.incident_id).where(Anomaly.incident_id.is_not(None))),
            ~Incident.id.in_(select(AnalysisRun.incident_id)),
        ))
        removed = 0
        for orphan in orphans:
            db.execute(delete(IncidentEvent).where(IncidentEvent.incident_id == orphan.id))
            db.delete(orphan)
            removed += 1
        db.commit()
        summary["environments"][env] = {
            **output.diagnostics, "incidents_created": created, "incidents_updated": updated, "incidents_removed": removed,
        }
    return summary
