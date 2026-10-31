"""SQLAlchemy ORM models.

All primary keys are UUIDs, all timestamps are timezone-aware UTC, flexible telemetry
attributes are stored as JSONB on PostgreSQL (plain JSON on SQLite for tests).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """Stores datetimes as UTC and always returns timezone-aware values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


ROLES = ("owner", "editor", "analyst", "viewer")


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    environments: Mapped[list] = mapped_column(JSONType, default=lambda: ["production"])
    analysis_settings: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),
        CheckConstraint("role in ('owner','editor','analyst','viewer')", name="ck_member_role"),
    )
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("project_id", "name", "environment", name="uq_service_name_env"),)
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    environment: Mapped[str] = mapped_column(String(64), default="production")
    kind: Mapped[str] = mapped_column(String(32), default="service")
    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_ingestion_idempotency"),
        CheckConstraint("status in ('queued','running','completed','failed')", name="ck_ingestion_status"),
        Index("ix_ingestion_project_created", "project_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(512))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(64), default="production")
    service_mapping: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSONType, default=list)
    detection_summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


EVENT_TYPES = ("log", "span", "metric", "deployment", "dependency")


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        UniqueConstraint("project_id", "event_hash", name="uq_event_hash"),
        CheckConstraint(
            "event_type in ('log','span','metric','deployment','dependency')", name="ck_event_type"
        ),
        Index("ix_events_project_time", "project_id", "occurred_at"),
        Index("ix_events_project_trace", "project_id", "trace_id"),
        Index("ix_events_service_time", "service_id", "occurred_at"),
        Index("ix_events_project_type_time", "project_id", "event_type", "occurred_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(64))
    parent_span_id: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str | None] = mapped_column(Text)
    metric_name: Mapped[str | None] = mapped_column(String(128))
    metric_value: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    status_code: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    event_hash: Mapped[str] = mapped_column(String(64))

    service: Mapped[Service] = relationship()


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_incident_fingerprint"),
        CheckConstraint("status in ('open','investigating','resolved')", name="ck_incident_status"),
        CheckConstraint("severity in ('critical','high','medium','low')", name="ck_incident_severity"),
        Index("ix_incidents_project_started", "project_id", "started_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="open")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    environment: Mapped[str] = mapped_column(String(64), default="production")
    fingerprint: Mapped[str] = mapped_column(String(64))
    affected_services: Mapped[list] = mapped_column(JSONType, default=list)
    timeline: Mapped[list] = mapped_column(JSONType, default=list)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    latest_analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_anomaly_fingerprint"),
        Index("ix_anomalies_project_start", "project_id", "window_start"),
    )
    id: Mapped[uuid.UUID] = pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    anomaly_type: Mapped[str] = mapped_column(String(48))
    metric: Mapped[str] = mapped_column(String(128))
    baseline: Mapped[float] = mapped_column(Float)
    observed: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16))
    window_start: Mapped[datetime] = mapped_column(UTCDateTime)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime)
    supporting_event_ids: Mapped[list] = mapped_column(JSONType, default=list)
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    service: Mapped[Service] = relationship()


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (UniqueConstraint("incident_id", "event_id", name="uq_incident_event"),)
    id: Mapped[uuid.UUID] = pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telemetry_events.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(24), default="context")

    event: Mapped[TelemetryEvent] = relationship()


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint("status in ('queued','running','completed','failed')", name="ck_analysis_status"),
        Index("ix_analysis_incident_created", "incident_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        back_populates="analysis_run", cascade="all, delete-orphan", order_by="Hypothesis.rank"
    )


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    __table_args__ = (UniqueConstraint("analysis_run_id", "rank", name="uq_hypothesis_rank"),)
    id: Mapped[uuid.UUID] = pk()
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    cause_type: Mapped[str] = mapped_column(String(48))
    service_name: Mapped[str] = mapped_column(String(160))
    ranking_score: Mapped[float] = mapped_column(Float)
    score_components: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[str] = mapped_column(Text)
    uncertainty: Mapped[str] = mapped_column(Text, default="")
    suggested_checks: Mapped[list] = mapped_column(JSONType, default=list)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="hypotheses")
    evidence: Mapped[list["HypothesisEvidence"]] = relationship(
        back_populates="hypothesis", cascade="all, delete-orphan"
    )


class HypothesisEvidence(Base):
    __tablename__ = "hypothesis_evidence"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "event_id", "polarity", name="uq_evidence"),
        CheckConstraint("polarity in ('supporting','contradicting')", name="ck_evidence_polarity"),
    )
    id: Mapped[uuid.UUID] = pk()
    hypothesis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hypotheses.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telemetry_events.id", ondelete="CASCADE"), index=True)
    polarity: Mapped[str] = mapped_column(String(16))
    explanation: Mapped[str] = mapped_column(Text)

    hypothesis: Mapped[Hypothesis] = relationship(back_populates="evidence")
    event: Mapped[TelemetryEvent] = relationship()


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"
    id: Mapped[uuid.UUID] = pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(128))
    content_markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_assistant_incident_created", "incident_id", "created_at"),)
    id: Mapped[uuid.UUID] = pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict] = mapped_column(JSONType, default=dict)
    model_version: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("verdict in ('correct','partially_correct','incorrect')", name="ck_feedback_verdict"),
    )
    id: Mapped[uuid.UUID] = pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hypotheses.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verdict: Mapped[str] = mapped_column(String(24))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint("status in ('queued','running','completed','failed')", name="ck_eval_status"),
    )
    id: Mapped[uuid.UUID] = pk()
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[int] = mapped_column(Integer)
    scenario_count: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "scenario_id", "strategy", name="uq_eval_result"),
    )
    id: Mapped[uuid.UUID] = pk()
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(48))
    fault_type: Mapped[str] = mapped_column(String(48))
    true_cause_service: Mapped[str] = mapped_column(String(160))
    predicted_services: Mapped[list] = mapped_column(JSONType, default=list)
    top1_correct: Mapped[bool] = mapped_column(Boolean)
    top3_correct: Mapped[bool] = mapped_column(Boolean)
    anomaly_tp: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_fp: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_fn: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_truth_total: Mapped[int] = mapped_column(Integer, default=0)
    detected: Mapped[bool] = mapped_column(Boolean, default=True)
    multi_fault: Mapped[bool] = mapped_column(Boolean, default=False)
    citations_total: Mapped[int] = mapped_column(Integer, default=0)
    citations_invalid: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float)
