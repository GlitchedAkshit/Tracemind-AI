"""Conversions between ORM rows and analysis-domain objects."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.types import Anomaly, Event
from app.models import Anomaly as AnomalyRow
from app.models import Service, TelemetryEvent

MAX_EVENTS_FOR_ANALYSIS = 400_000

_COLUMNS = (
    TelemetryEvent.id, Service.name, TelemetryEvent.event_type, TelemetryEvent.occurred_at, TelemetryEvent.severity,
    TelemetryEvent.message, TelemetryEvent.trace_id, TelemetryEvent.span_id, TelemetryEvent.parent_span_id,
    TelemetryEvent.metric_name, TelemetryEvent.metric_value, TelemetryEvent.duration_ms, TelemetryEvent.status_code,
    TelemetryEvent.payload,
)


def load_events(
    db: Session, project_id: uuid.UUID, environment: str | None = None,
    start: datetime | None = None, end: datetime | None = None, limit: int = MAX_EVENTS_FOR_ANALYSIS,
) -> list[Event]:
    query = select(*_COLUMNS).join(Service, Service.id == TelemetryEvent.service_id).where(TelemetryEvent.project_id == project_id)
    if environment:
        query = query.where(Service.environment == environment)
    if start is not None:
        query = query.where(TelemetryEvent.occurred_at >= start)
    if end is not None:
        query = query.where(TelemetryEvent.occurred_at <= end)
    query = query.order_by(TelemetryEvent.occurred_at).limit(limit)
    events = []
    for row in db.execute(query):
        occurred = row.occurred_at
        events.append(Event(
            id=str(row.id), service=row.name, event_type=row.event_type, occurred_at=occurred,
            severity=row.severity, message=row.message, trace_id=row.trace_id, span_id=row.span_id,
            parent_span_id=row.parent_span_id, metric_name=row.metric_name, metric_value=row.metric_value,
            duration_ms=row.duration_ms, status_code=row.status_code, attributes=row.payload or {},
        ))
    return events


def anomaly_from_row(row: AnomalyRow) -> Anomaly:
    return Anomaly(
        id=row.fingerprint, service=row.service.name, anomaly_type=row.anomaly_type, metric=row.metric,
        baseline=row.baseline, observed=row.observed, score=row.score, severity=row.severity,
        window_start=row.window_start, window_end=row.window_end,
        supporting_event_ids=[str(i) for i in row.supporting_event_ids], details=row.details or {},
    )


def event_detail(event: TelemetryEvent) -> dict:
    return {
        "id": str(event.id),
        "service": event.service.name,
        "environment": event.service.environment,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "ingested_at": event.ingested_at,
        "severity": event.severity,
        "message": event.message,
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "metric_name": event.metric_name,
        "metric_value": event.metric_value,
        "duration_ms": event.duration_ms,
        "status_code": event.status_code,
        "attributes": event.payload or {},
        "ingestion_job_id": str(event.ingestion_job_id) if event.ingestion_job_id else None,
        "event_hash": event.event_hash,
    }
