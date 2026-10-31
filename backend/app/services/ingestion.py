"""Ingestion jobs: store the raw file, parse/validate/normalise, persist, then detect."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal
from app.errors import ApiError
from app.ingestion.normalize import NormalizedRecord, detect_format, parse_file
from app.models import IngestionJob, Project, Service, TelemetryEvent, User
from app.services import jobs
from app.services.detection import detect_project
from app.storage import get_storage, object_key

log = logging.getLogger("tracemind.ingestion")
CHUNK = 2000
ALLOWED_EXTENSIONS = (".jsonl", ".ndjson", ".json", ".csv", ".tsv")


def parse_mapping(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(422, "invalid_mapping", "service_mapping must be a JSON object of strings.") from exc
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise ApiError(422, "invalid_mapping", "service_mapping must be a JSON object of strings.")
    if len(mapping) > 500:
        raise ApiError(422, "invalid_mapping", "service_mapping supports at most 500 entries.")
    return {k.strip(): v.strip() for k, v in mapping.items()}


def validate_upload(filename: str, content: bytes) -> str:
    settings = get_settings()
    if not filename or not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise ApiError(415, "unsupported_file", "Upload a .jsonl, .ndjson, .json, .csv or .tsv file.")
    if len(content) == 0:
        raise ApiError(422, "empty_file", "The uploaded file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise ApiError(413, "payload_too_large", f"Files are limited to {settings.max_upload_bytes // (1024 * 1024)} MB.")
    if b"\x00" in content[:8192]:
        raise ApiError(415, "unsupported_file", "Binary files are not accepted.")
    return detect_format(filename, content[:256])


def preview(content: bytes, filename: str, mapping: dict[str, str], environment: str) -> dict:
    validate_upload(filename, content)
    result = parse_file(content, filename, mapping, environment, limit=500)
    return {
        "format": detect_format(filename, content[:256]),
        "sampled_rows": result.total_rows,
        "accepted_rows": len(result.records),
        "rejected_rows": result.rejected_rows,
        "duplicate_rows": result.duplicate_rows,
        "columns": [{"name": k, "present_in_rows": v} for k, v in sorted(result.columns.items(), key=lambda kv: -kv[1])],
        "services": sorted({r.service for r in result.records}),
        "event_types": sorted({r.event_type for r in result.records}),
        "warnings": result.warnings,
        "errors": result.errors[:50],
        "records": [r.to_preview() for r in result.records[:25]],
    }


def create_job(db: Session, project: Project, user: User, filename: str, content: bytes, environment: str,
               mapping: dict[str, str], idempotency_key: str | None) -> tuple[IngestionJob, bool]:
    file_format = validate_upload(filename, content)
    if idempotency_key:
        existing = db.scalar(select(IngestionJob).where(IngestionJob.project_id == project.id,
                                                        IngestionJob.idempotency_key == idempotency_key))
        if existing:
            return existing, False
    job = IngestionJob(
        id=uuid.uuid4(), project_id=project.id, created_by=user.id, filename=filename[:255], file_format=file_format,
        size_bytes=len(content), content_sha256=hashlib.sha256(content).hexdigest(), idempotency_key=idempotency_key,
        environment=environment, service_mapping=mapping, status="queued",
    )
    job.storage_key = get_storage().put(object_key(str(project.id), str(job.id), filename), content)
    db.add(job)
    db.commit()
    jobs.submit(run_ingestion, job.id)
    return job, True


def _service_ids(db: Session, project_id: uuid.UUID, records: list[NormalizedRecord]) -> dict[tuple[str, str], uuid.UUID]:
    keys = {(r.service, r.environment) for r in records}
    for r in records:
        if r.event_type == "dependency":
            keys.add((r.attributes["target"], r.environment))
    existing = {
        (s.name, s.environment): s for s in db.scalars(select(Service).where(Service.project_id == project_id))
    }
    for name, env in keys - set(existing):
        service = Service(id=uuid.uuid4(), project_id=project_id, name=name, environment=env,
                          kind="database" if name.endswith("-db") else "service")
        db.add(service)
        existing[(name, env)] = service
    times: dict[tuple[str, str], list[datetime]] = {}
    for r in records:
        bounds = times.setdefault((r.service, r.environment), [r.occurred_at, r.occurred_at])
        bounds[0] = min(bounds[0], r.occurred_at)
        bounds[1] = max(bounds[1], r.occurred_at)
    for key, (first, last) in times.items():
        s = existing[key]
        s.first_seen_at = min(s.first_seen_at, first) if s.first_seen_at else first
        s.last_seen_at = max(s.last_seen_at, last) if s.last_seen_at else last
    db.flush()
    return {key: s.id for key, s in existing.items()}


def ingest_records(db: Session, job: IngestionJob, records: list[NormalizedRecord]) -> tuple[int, int]:
    """Persist records, skipping events already stored for the project. Returns (accepted, duplicates)."""
    service_ids = _service_ids(db, job.project_id, records)
    accepted = duplicates = 0
    now = datetime.now(UTC)
    for start in range(0, len(records), CHUNK):
        chunk = records[start:start + CHUNK]
        hashes = [r.event_hash for r in chunk]
        known = set(db.scalars(select(TelemetryEvent.event_hash).where(
            TelemetryEvent.project_id == job.project_id, TelemetryEvent.event_hash.in_(hashes))))
        rows = []
        for r in chunk:
            if r.event_hash in known:
                duplicates += 1
                continue
            rows.append({
                "id": uuid.uuid4(), "project_id": job.project_id, "service_id": service_ids[(r.service, r.environment)],
                "ingestion_job_id": job.id, "event_type": r.event_type, "occurred_at": r.occurred_at, "ingested_at": now,
                "trace_id": r.trace_id, "span_id": r.span_id, "parent_span_id": r.parent_span_id, "severity": r.severity,
                "message": r.message, "metric_name": r.metric_name, "metric_value": r.metric_value,
                "duration_ms": r.duration_ms, "status_code": r.status_code, "payload": r.attributes, "event_hash": r.event_hash,
            })
        if rows:
            db.execute(insert(TelemetryEvent), rows)
        accepted += len(rows)
        job.processed_rows = min(job.total_rows, job.rejected_rows + job.duplicate_rows + start + len(chunk))
        db.commit()
    return accepted, duplicates


def run_ingestion(job_id: uuid.UUID, detect: bool = True) -> None:
    db = SessionLocal()
    try:
        job = db.get(IngestionJob, job_id)
        if job is None or job.status not in ("queued", "running"):
            return
        job.status, job.started_at = "running", datetime.now(UTC)
        db.commit()
        settings = get_settings()
        content = get_storage().get(job.storage_key)
        result = parse_file(content, job.filename, job.service_mapping, job.environment, max_rows=settings.max_rows_per_upload)
        job.total_rows = result.total_rows
        job.rejected_rows = result.rejected_rows
        job.duplicate_rows = result.duplicate_rows
        job.errors = result.errors
        db.commit()
        accepted, db_duplicates = ingest_records(db, job, result.records)
        job.accepted_rows = accepted
        job.duplicate_rows = result.duplicate_rows + db_duplicates
        job.processed_rows = job.total_rows
        db.commit()
        if detect and accepted:
            envs = sorted({r.environment for r in result.records})
            job.detection_summary = detect_project(db, job.project_id, envs)
        job.status, job.completed_at = "completed", datetime.now(UTC)
        db.commit()
        log.info("ingestion completed", extra={"job_id": str(job.id), "accepted": accepted, "rejected": job.rejected_rows})
    except Exception as exc:
        db.rollback()
        log.exception("ingestion failed", extra={"job_id": str(job_id)})
        job = db.get(IngestionJob, job_id)
        if job is not None:
            job.status, job.completed_at = "failed", datetime.now(UTC)
            job.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
    finally:
        db.close()
