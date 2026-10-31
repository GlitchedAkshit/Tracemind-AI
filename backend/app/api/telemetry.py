"""Services, dependency graph and raw telemetry browsing."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.errors import ApiError
from app.models import Incident, Service, TelemetryEvent, User
from app.schemas import EventOut, Page, ServiceOut
from app.security import current_user, require_incident, require_project
from app.services.events import event_detail
from app.services.overview import service_graph

router = APIRouter(tags=["telemetry"])


@router.get("/projects/{project_id}/services", response_model=list[ServiceOut])
def list_services(project_id: uuid.UUID, environment: str | None = None, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    counts = (select(TelemetryEvent.service_id, func.count(TelemetryEvent.id).label("n"),
                     func.sum(case((TelemetryEvent.severity.in_(("error", "critical", "fatal")), 1), else_=0)).label("errors"))
              .where(TelemetryEvent.project_id == project_id).group_by(TelemetryEvent.service_id).subquery())
    query = (select(Service, counts.c.n, counts.c.errors).outerjoin(counts, counts.c.service_id == Service.id)
             .where(Service.project_id == project_id).order_by(Service.name))
    if environment:
        query = query.where(Service.environment == environment)
    open_incidents = list(db.scalars(select(Incident).where(Incident.project_id == project_id, Incident.status != "resolved")))
    out = []
    for service, n, errors in db.execute(query):
        item = ServiceOut.model_validate(service)
        item.event_count, item.error_count = n or 0, errors or 0
        item.open_incidents = sum(1 for i in open_incidents if service.name in i.affected_services and i.environment == service.environment)
        out.append(item)
    return out


@router.get("/projects/{project_id}/service-graph")
def get_service_graph(project_id: uuid.UUID, environment: str | None = None, incident_id: uuid.UUID | None = None,
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    incident = None
    if incident_id is not None:
        incident = require_incident(db, user, incident_id)
        if incident.project_id != project_id:
            raise ApiError(404, "not_found", "Incident not found.")
    return service_graph(db, project_id, environment, incident)


@router.get("/projects/{project_id}/events", response_model=Page[EventOut])
def list_events(project_id: uuid.UUID, service: str | None = None, event_type: str | None = None,
                severity: str | None = None, trace_id: str | None = Query(None, max_length=64),
                q: str | None = Query(None, max_length=200), start: datetime | None = None, end: datetime | None = None,
                page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=200),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    query = select(TelemetryEvent).join(Service, Service.id == TelemetryEvent.service_id).where(TelemetryEvent.project_id == project_id)
    if service:
        query = query.where(Service.name == service)
    if event_type:
        query = query.where(TelemetryEvent.event_type == event_type)
    if severity:
        query = query.where(TelemetryEvent.severity == severity)
    if trace_id:
        query = query.where(TelemetryEvent.trace_id == trace_id)
    if q:
        query = query.where(TelemetryEvent.message.ilike(f"%{q.replace('%', '').replace('_', '')}%"))
    if start:
        query = query.where(TelemetryEvent.occurred_at >= start)
    if end:
        query = query.where(TelemetryEvent.occurred_at <= end)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.options(selectinload(TelemetryEvent.service))
                      .order_by(TelemetryEvent.occurred_at.desc(), TelemetryEvent.id).offset((page - 1) * page_size).limit(page_size))
    return Page(items=[EventOut(**event_detail(e)) for e in rows], total=total, page=page, page_size=page_size)


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = db.get(TelemetryEvent, event_id)
    if event is None:
        raise ApiError(404, "not_found", "Event not found.")
    try:
        require_project(db, user, event.project_id)
    except ApiError as exc:
        raise ApiError(404, "not_found", "Event not found.") from exc
    return EventOut(**event_detail(event))
