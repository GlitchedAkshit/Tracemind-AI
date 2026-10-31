"""Asynchronous incident analysis runs: rank hypotheses, link evidence, write a summary."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.analysis.graph import build_graph
from app.analysis.ranking import DEFAULT_STRATEGY, STRATEGIES, rank_hypotheses
from app.analysis.types import ALGORITHM_VERSION, AnalysisConfig, CandidateIncident
from app.db import SessionLocal
from app.errors import ApiError
from app.llm.evidence import build_bundle
from app.llm.providers import get_provider
from app.llm.writer import DETERMINISTIC_MODEL, write_summary
from app.models import AnalysisRun, Anomaly, Hypothesis, HypothesisEvidence, Incident, IncidentEvent, Project, TelemetryEvent, User
from app.services import jobs
from app.services.events import anomaly_from_row, event_detail, load_events

log = logging.getLogger("tracemind.analysis")


def create_run(db: Session, incident: Incident, user: User, strategy: str = DEFAULT_STRATEGY) -> AnalysisRun:
    if strategy not in STRATEGIES:
        raise ApiError(422, "invalid_strategy", f"Unknown strategy {strategy!r}.")
    active = db.scalar(select(AnalysisRun).where(AnalysisRun.incident_id == incident.id,
                                                 AnalysisRun.status.in_(("queued", "running"))))
    if active is not None:
        return active  # idempotent: one in-flight analysis per incident
    provider = get_provider()
    project = db.get(Project, incident.project_id)
    run = AnalysisRun(
        id=uuid.uuid4(), incident_id=incident.id, created_by=user.id, algorithm_version=ALGORITHM_VERSION,
        model_version=provider.model_version if provider else DETERMINISTIC_MODEL, status="queued", progress=0,
        stage="queued", config={"strategy": strategy, **AnalysisConfig.from_dict(project.analysis_settings).to_dict()},
    )
    db.add(run)
    incident.status = "investigating" if incident.status == "open" else incident.status
    db.commit()
    jobs.submit(execute_run, run.id)
    return run


def _stage(db: Session, run: AnalysisRun, stage: str, progress: int) -> None:
    run.stage, run.progress = stage, progress
    db.commit()


def candidate_from_incident(db: Session, incident: Incident) -> CandidateIncident:
    anomalies = [anomaly_from_row(a) for a in db.scalars(
        select(Anomaly).options(selectinload(Anomaly.service)).where(Anomaly.incident_id == incident.id))]
    deployments = [str(i) for i in db.scalars(select(IncidentEvent.event_id).where(
        IncidentEvent.incident_id == incident.id, IncidentEvent.role == "deployment"))]
    return CandidateIncident(
        fingerprint=incident.fingerprint, title=incident.title, severity=incident.severity,
        started_at=incident.started_at, ended_at=incident.ended_at or incident.started_at,
        services=list(incident.affected_services), anomalies=anomalies, deployment_event_ids=deployments, timeline=[],
    )


def execute_run(run_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        run = db.get(AnalysisRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status, run.started_at = "running", datetime.now(UTC)
        _stage(db, run, "loading telemetry", 10)
        incident = db.get(Incident, run.incident_id)
        cfg = AnalysisConfig.from_dict(run.config)
        candidate = candidate_from_incident(db, incident)
        if not candidate.anomalies:
            raise ValueError("incident has no anomalies to analyse")
        window_start = candidate.started_at - timedelta(minutes=cfg.deployment_lookback_minutes)
        window_end = candidate.ended_at + timedelta(minutes=5)
        events = load_events(db, incident.project_id, incident.environment, window_start, window_end)

        _stage(db, run, "building dependency graph", 30)
        graph = build_graph(events)

        _stage(db, run, "ranking hypotheses", 50)
        ranked = rank_hypotheses(candidate, events, graph, cfg, run.config.get("strategy", DEFAULT_STRATEGY))

        _stage(db, run, "linking evidence", 65)
        for h in ranked:
            row = Hypothesis(
                analysis_run_id=run.id, rank=h.rank, cause_type=h.cause_type, service_name=h.service,
                ranking_score=h.ranking_score, score_components=h.components, summary=h.summary,
                uncertainty=h.uncertainty, suggested_checks=h.suggested_checks,
            )
            row.evidence = [
                HypothesisEvidence(event_id=uuid.UUID(ev.event_id), polarity=ev.polarity, explanation=ev.explanation)
                for ev in h.evidence
            ]
            db.add(row)
        db.flush()

        _stage(db, run, "writing investigation summary", 80)
        hyp_dicts = [
            {"rank": h.rank, "service": h.service, "cause_type": h.cause_type, "ranking_score": h.ranking_score,
             "components": h.components, "summary": h.summary, "uncertainty": h.uncertainty,
             "evidence": [{"event_id": ev.event_id, "polarity": ev.polarity, "explanation": ev.explanation} for ev in h.evidence]}
            for h in ranked
        ]
        referenced = {ev["event_id"] for h in hyp_dicts for ev in h["evidence"]} | set(candidate.deployment_event_ids)
        details = [event_detail(e) for e in db.scalars(select(TelemetryEvent).options(selectinload(TelemetryEvent.service))
                                                       .where(TelemetryEvent.id.in_([uuid.UUID(i) for i in referenced])))]
        bundle = build_bundle(
            {"title": incident.title, "severity": incident.severity, "started_at": incident.started_at,
             "ended_at": incident.ended_at, "affected_services": incident.affected_services},
            hyp_dicts, details, extra_event_ids=candidate.deployment_event_ids,
        )
        provider = get_provider()
        summary, meta = write_summary(bundle, provider, ranked[0].suggested_checks if ranked else [])
        run.summary = {**summary, "meta": meta, "suspicious_event_ids": [bundle.alias_to_event[a] for a in bundle.suspicious_aliases]}
        run.model_version = meta["model_version"]
        incident.latest_analysis_run_id = run.id
        run.status, run.stage, run.progress, run.completed_at = "completed", "completed", 100, datetime.now(UTC)
        db.commit()
        log.info("analysis completed", extra={"run_id": str(run.id), "hypotheses": len(ranked), "writer": meta["writer"]})
    except Exception as exc:
        db.rollback()
        log.exception("analysis failed", extra={"run_id": str(run_id)})
        run = db.get(AnalysisRun, run_id)
        if run is not None:
            run.status, run.stage, run.completed_at = "failed", "failed", datetime.now(UTC)
            run.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
    finally:
        db.close()


def latest_completed_run(db: Session, incident: Incident) -> AnalysisRun | None:
    return db.scalar(select(AnalysisRun).where(AnalysisRun.incident_id == incident.id, AnalysisRun.status == "completed")
                     .order_by(AnalysisRun.completed_at.desc()).limit(1))
