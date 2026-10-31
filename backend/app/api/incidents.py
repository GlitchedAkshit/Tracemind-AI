import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.errors import ApiError
from app.llm.evidence import build_bundle
from app.llm.providers import get_provider
from app.llm.writer import answer_question
from app.models import (
    AnalysisRun,
    Anomaly,
    AssistantMessage,
    Feedback,
    Hypothesis,
    HypothesisEvidence,
    Incident,
    InvestigationReport,
    TelemetryEvent,
    User,
)
from app.ratelimit import limiter
from app.reports.markdown import render_report, report_filename
from app.schemas import (
    AnalysisRunOut,
    AnalyzeIn,
    AnomalyOut,
    EvidenceOut,
    FeedbackIn,
    FeedbackOut,
    HypothesesOut,
    HypothesisOut,
    IncidentOut,
    IncidentPatch,
    InvestigateIn,
    InvestigationOut,
    Page,
    ReportOut,
    TimelineEntryOut,
)
from app.security import current_user, require_incident, require_project
from app.services.analysis import create_run, latest_completed_run
from app.services.events import event_detail
from app.services.overview import incident_metrics

router = APIRouter(tags=["incidents"])
SORTS = {"started_at": Incident.started_at, "severity": Incident.severity, "status": Incident.status, "title": Incident.title}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _incident_out(db: Session, incident: Incident) -> IncidentOut:
    out = IncidentOut.model_validate(incident)
    latest = db.scalar(select(AnalysisRun).where(AnalysisRun.incident_id == incident.id).order_by(AnalysisRun.created_at.desc()).limit(1))
    out.analysis_status = latest.status if latest else None
    if incident.latest_analysis_run_id:
        top = db.scalar(select(Hypothesis).where(Hypothesis.analysis_run_id == incident.latest_analysis_run_id, Hypothesis.rank == 1))
        if top:
            out.leading_hypothesis, out.leading_cause_type = top.service_name, top.cause_type
    out.anomaly_count = db.scalar(select(func.count(Anomaly.id)).where(Anomaly.incident_id == incident.id)) or 0
    return out


@router.get("/projects/{project_id}/incidents", response_model=Page[IncidentOut])
def list_incidents(project_id: uuid.UUID, status: str | None = None, severity: str | None = None,
                   environment: str | None = None, q: str | None = Query(None, max_length=200),
                   sort: str = Query("started_at"), order: str = Query("desc", pattern="^(asc|desc)$"),
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    query = select(Incident).where(Incident.project_id == project_id)
    if status:
        query = query.where(Incident.status.in_(status.split(",")))
    if severity:
        query = query.where(Incident.severity.in_(severity.split(",")))
    if environment:
        query = query.where(Incident.environment == environment)
    if q:
        term = f"%{q.replace('%', '').replace('_', '')}%"
        query = query.where(or_(Incident.title.ilike(term), Incident.fingerprint.ilike(term)))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    if sort not in SORTS:
        raise ApiError(422, "invalid_sort", f"sort must be one of {', '.join(SORTS)}")
    column = SORTS[sort]
    if sort == "severity":
        from sqlalchemy import case
        column = case(SEVERITY_RANK, value=Incident.severity)
        order = "asc" if order == "desc" else "desc"  # "desc" severity = most severe first
    query = query.order_by(column.desc() if order == "desc" else column.asc(), Incident.id)
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[_incident_out(db, i) for i in items], total=total, page=page, page_size=page_size)


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _incident_out(db, require_incident(db, user, incident_id))


@router.patch("/incidents/{incident_id}", response_model=IncidentOut)
def update_incident(incident_id: uuid.UUID, body: IncidentPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = require_incident(db, user, incident_id, "analyst")
    incident.status = body.status
    db.commit()
    return _incident_out(db, incident)


@router.get("/incidents/{incident_id}/timeline", response_model=list[TimelineEntryOut])
def get_timeline(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return require_incident(db, user, incident_id).timeline or []


@router.get("/incidents/{incident_id}/anomalies", response_model=list[AnomalyOut])
def get_anomalies(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_incident(db, user, incident_id)
    rows = db.scalars(select(Anomaly).options(selectinload(Anomaly.service)).where(Anomaly.incident_id == incident_id)
                      .order_by(Anomaly.window_start))
    return [AnomalyOut(id=a.id, service=a.service.name, anomaly_type=a.anomaly_type, metric=a.metric, baseline=a.baseline,
                       observed=a.observed, score=a.score, severity=a.severity, window_start=a.window_start,
                       window_end=a.window_end, supporting_event_ids=[str(i) for i in a.supporting_event_ids],
                       incident_id=a.incident_id, details=a.details or {}) for a in rows]


@router.get("/incidents/{incident_id}/metrics")
def get_metrics(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return incident_metrics(db, require_incident(db, user, incident_id))


def _evidence(e: HypothesisEvidence) -> EvidenceOut:
    ev = e.event
    return EvidenceOut(event_id=e.event_id, polarity=e.polarity, explanation=e.explanation, occurred_at=ev.occurred_at,
                       service=ev.service.name, event_type=ev.event_type, severity=ev.severity, message=ev.message)


def _hypotheses(db: Session, run_id: uuid.UUID) -> list[HypothesisOut]:
    rows = db.scalars(select(Hypothesis).options(selectinload(Hypothesis.evidence).selectinload(HypothesisEvidence.event)
                                                 .selectinload(TelemetryEvent.service))
                      .where(Hypothesis.analysis_run_id == run_id).order_by(Hypothesis.rank))
    return [HypothesisOut(id=h.id, rank=h.rank, cause_type=h.cause_type, service=h.service_name, ranking_score=h.ranking_score,
                          score_components=h.score_components, summary=h.summary, uncertainty=h.uncertainty,
                          suggested_checks=h.suggested_checks, evidence=[_evidence(e) for e in h.evidence]) for h in rows]


@router.get("/incidents/{incident_id}/hypotheses", response_model=HypothesesOut)
def get_hypotheses(incident_id: uuid.UUID, run_id: uuid.UUID | None = None, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    incident = require_incident(db, user, incident_id)
    run = db.get(AnalysisRun, run_id) if run_id else latest_completed_run(db, incident)
    if run_id and (run is None or run.incident_id != incident.id):
        raise ApiError(404, "not_found", "Analysis run not found.")
    if run is None:
        return HypothesesOut(analysis_run=None, hypotheses=[])
    return HypothesesOut(analysis_run=AnalysisRunOut.model_validate(run), hypotheses=_hypotheses(db, run.id))


@router.post("/incidents/{incident_id}/analyze", response_model=AnalysisRunOut, status_code=202)
def analyze(incident_id: uuid.UUID, body: AnalyzeIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = require_incident(db, user, incident_id, "analyst")
    limiter.check(f"analyze:{user.id}", get_settings().analysis_rate_limit_per_minute)
    return create_run(db, incident, user, (body or AnalyzeIn()).strategy)


@router.get("/incidents/{incident_id}/analysis-runs", response_model=list[AnalysisRunOut])
def list_runs(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_incident(db, user, incident_id)
    return db.scalars(select(AnalysisRun).where(AnalysisRun.incident_id == incident_id).order_by(AnalysisRun.created_at.desc()).limit(50)).all()


def _authorized_run(db: Session, user: User, run_id: uuid.UUID) -> AnalysisRun:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Analysis run not found.")
    try:
        require_incident(db, user, run.incident_id)
    except ApiError as exc:
        raise ApiError(404, "not_found", "Analysis run not found.") from exc
    return run


@router.get("/analysis-runs/{run_id}", response_model=AnalysisRunOut)
def get_run(run_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _authorized_run(db, user, run_id)


@router.get("/analysis-runs/{run_id}/events")
async def stream_run(run_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Server-sent events with run progress until the run completes or fails (max 3 minutes)."""
    _authorized_run(db, user, run_id)

    async def events():
        last = None
        for _ in range(360):
            if await request.is_disconnected():
                return
            with SessionLocal() as session:
                run = session.get(AnalysisRun, run_id)
                payload = {"status": run.status, "stage": run.stage, "progress": run.progress, "error_message": run.error_message}
            if payload != last:
                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                last = payload
            if payload["status"] in ("completed", "failed"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------------------ assistant

def _bundle_for(db: Session, incident: Incident, run: AnalysisRun | None):
    hyps = _hypotheses(db, run.id) if run else []
    hyp_dicts = [{"rank": h.rank, "service": h.service, "cause_type": h.cause_type, "ranking_score": h.ranking_score,
                  "components": h.score_components, "summary": h.summary,
                  "evidence": [{"event_id": str(e.event_id), "polarity": e.polarity, "explanation": e.explanation} for e in h.evidence]}
                 for h in hyps]
    timeline_ids = [t["event_id"] for t in incident.timeline or [] if t.get("event_id")]
    wanted = {ev["event_id"] for h in hyp_dicts for ev in h["evidence"]} | set(timeline_ids)
    rows = db.scalars(select(TelemetryEvent).options(selectinload(TelemetryEvent.service))
                      .where(TelemetryEvent.id.in_([uuid.UUID(i) for i in wanted]))) if wanted else []
    details = [event_detail(e) for e in rows]
    bundle = build_bundle({"title": incident.title, "severity": incident.severity, "started_at": incident.started_at,
                           "ended_at": incident.ended_at, "affected_services": incident.affected_services},
                          hyp_dicts, details, extra_event_ids=timeline_ids)
    return bundle, (hyps[0].suggested_checks if hyps else [])


def _investigation_out(db: Session, msg: AssistantMessage) -> InvestigationOut:
    a = msg.answer
    ids = [uuid.UUID(i) for i in a.get("evidence_ids", [])]
    events = {e.id: e for e in db.scalars(select(TelemetryEvent).options(selectinload(TelemetryEvent.service))
                                          .where(TelemetryEvent.id.in_(ids)))} if ids else {}
    evidence = [EvidenceOut(event_id=e.id, polarity="cited", explanation="Cited by the assistant", occurred_at=e.occurred_at,
                            service=e.service.name, event_type=e.event_type, severity=e.severity, message=e.message)
                for i in ids if (e := events.get(i))]
    meta = a.get("meta", {})
    return InvestigationOut(id=msg.id, question=msg.question, answer=a.get("answer", ""), evidence=evidence,
                            uncertainties=a.get("uncertainties", []), suggested_checks=a.get("suggested_checks", []),
                            insufficient_evidence=a.get("insufficient_evidence", False), model_version=msg.model_version,
                            writer=meta.get("writer", "deterministic"), fallback_reason=meta.get("fallback_reason"),
                            citations_rejected=meta.get("citations_rejected", 0), created_at=msg.created_at)


@router.post("/incidents/{incident_id}/investigate", response_model=InvestigationOut)
def investigate(incident_id: uuid.UUID, body: InvestigateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = require_incident(db, user, incident_id, "analyst")
    limiter.check(f"investigate:{user.id}", get_settings().investigate_rate_limit_per_minute)
    bundle, checks = _bundle_for(db, incident, latest_completed_run(db, incident))
    answer, meta = answer_question(body.question.strip(), bundle, get_provider(), checks)
    msg = AssistantMessage(incident_id=incident.id, user_id=user.id, question=body.question.strip(),
                           answer={**answer, "meta": meta}, model_version=meta["model_version"])
    db.add(msg)
    db.commit()
    return _investigation_out(db, msg)


@router.get("/incidents/{incident_id}/investigations", response_model=list[InvestigationOut])
def list_investigations(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_incident(db, user, incident_id)
    rows = db.scalars(select(AssistantMessage).where(AssistantMessage.incident_id == incident_id)
                      .order_by(AssistantMessage.created_at).limit(100))
    return [_investigation_out(db, m) for m in rows]


# ------------------------------------------------------------------------------ feedback & reports

@router.post("/incidents/{incident_id}/feedback", response_model=FeedbackOut, status_code=201)
def create_feedback(incident_id: uuid.UUID, body: FeedbackIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = require_incident(db, user, incident_id, "analyst")
    if body.hypothesis_id:
        hyp = db.get(Hypothesis, body.hypothesis_id)
        run = db.get(AnalysisRun, hyp.analysis_run_id) if hyp else None
        if run is None or run.incident_id != incident.id:
            raise ApiError(422, "invalid_hypothesis", "Hypothesis does not belong to this incident.")
        body.analysis_run_id = run.id
    elif body.analysis_run_id:
        run = db.get(AnalysisRun, body.analysis_run_id)
        if run is None or run.incident_id != incident.id:
            raise ApiError(422, "invalid_run", "Analysis run does not belong to this incident.")
    fb = Feedback(incident_id=incident.id, analysis_run_id=body.analysis_run_id, hypothesis_id=body.hypothesis_id,
                  user_id=user.id, verdict=body.verdict, comment=body.comment.strip())
    db.add(fb)
    db.commit()
    return fb


@router.get("/incidents/{incident_id}/feedback", response_model=list[FeedbackOut])
def list_feedback(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_incident(db, user, incident_id)
    return db.scalars(select(Feedback).where(Feedback.incident_id == incident_id).order_by(Feedback.created_at.desc())).all()


@router.post("/incidents/{incident_id}/reports", response_model=ReportOut, status_code=201)
def create_report(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    incident = require_incident(db, user, incident_id, "analyst")
    run = latest_completed_run(db, incident)
    if run is None:
        raise ApiError(409, "analysis_required", "Run an analysis before generating a report.")
    report = InvestigationReport(incident_id=incident.id, analysis_run_id=run.id, created_by=user.id,
                                 algorithm_version=run.algorithm_version, model_version=run.model_version,
                                 content_markdown=render_report(db, incident, run))
    db.add(report)
    db.commit()
    return report


@router.get("/incidents/{incident_id}/reports", response_model=list[ReportOut])
def list_reports(incident_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_incident(db, user, incident_id)
    return db.scalars(select(InvestigationReport).where(InvestigationReport.incident_id == incident_id)
                      .order_by(InvestigationReport.created_at.desc())).all()


@router.get("/projects/{project_id}/reports")
def project_reports(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    rows = db.execute(select(InvestigationReport, Incident).join(Incident, Incident.id == InvestigationReport.incident_id)
                      .where(Incident.project_id == project_id).order_by(InvestigationReport.created_at.desc()).limit(100))
    return [{"id": str(r.id), "incident_id": str(i.id), "incident_title": i.title, "severity": i.severity,
             "algorithm_version": r.algorithm_version, "model_version": r.model_version, "created_at": r.created_at}
            for r, i in rows]


def _authorized_report(db: Session, user: User, report_id: uuid.UUID) -> InvestigationReport:
    report = db.get(InvestigationReport, report_id)
    if report is None:
        raise ApiError(404, "not_found", "Report not found.")
    try:
        require_incident(db, user, report.incident_id)
    except ApiError as exc:
        raise ApiError(404, "not_found", "Report not found.") from exc
    return report


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _authorized_report(db, user, report_id)


@router.get("/reports/{report_id}/markdown", response_class=PlainTextResponse)
def download_report(report_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    report = _authorized_report(db, user, report_id)
    incident = db.get(Incident, report.incident_id)
    return PlainTextResponse(report.content_markdown, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{report_filename(incident, report.id)}"'})
