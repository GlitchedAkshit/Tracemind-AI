"""Pydantic request/response schemas for the v1 API."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ----------------------------------------------------------------------------- auth

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    display_name: str
    is_demo: bool


class SessionOut(BaseModel):
    user: UserOut
    token: str


# ----------------------------------------------------------------------------- projects

ANALYSIS_SETTING_KEYS = {
    "z_threshold": (2.0, 20.0), "min_error_rate": (0.001, 1.0), "min_error_count": (1, 100),
    "min_latency_delta_ms": (1.0, 60000.0), "min_latency_ratio": (1.0, 50.0), "baseline_buckets": (5, 500),
    "min_baseline_points": (3, 200), "bucket_seconds": (10, 3600), "correlation_gap_minutes": (0.5, 120.0),
    "deployment_lookback_minutes": (1.0, 720.0),
}


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    environments: list[str] = Field(default_factory=lambda: ["production"], min_length=1, max_length=20)

    @field_validator("environments")
    @classmethod
    def _envs(cls, value: list[str]) -> list[str]:
        cleaned = []
        for env in value:
            env = env.strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", env):
                raise ValueError(f"invalid environment name {env!r}")
            cleaned.append(env)
        return list(dict.fromkeys(cleaned))


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    environments: list[str] | None = None
    analysis_settings: dict[str, float] | None = None

    @field_validator("environments")
    @classmethod
    def _envs(cls, value):
        return ProjectIn._envs(value) if value is not None else value

    @field_validator("analysis_settings")
    @classmethod
    def _settings(cls, value):
        if value is None:
            return value
        for key, number in value.items():
            if key not in ANALYSIS_SETTING_KEYS:
                raise ValueError(f"unknown analysis setting {key!r}")
            low, high = ANALYSIS_SETTING_KEYS[key]
            if not low <= number <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
        return value


class ProjectOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    is_demo: bool
    environments: list[str]
    analysis_settings: dict
    created_at: datetime
    role: str | None = None


class MemberIn(BaseModel):
    email: EmailStr
    role: Literal["owner", "editor", "analyst", "viewer"]


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    created_at: datetime


# ----------------------------------------------------------------------------- ingestion

class IngestionOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    file_format: str
    size_bytes: int
    content_sha256: str
    environment: str
    service_mapping: dict
    status: str
    total_rows: int
    processed_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    errors: list
    detection_summary: dict
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


# ----------------------------------------------------------------------------- services/events

class ServiceOut(ORM):
    id: uuid.UUID
    name: str
    environment: str
    kind: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    event_count: int = 0
    error_count: int = 0
    open_incidents: int = 0


class EventOut(BaseModel):
    id: str
    service: str
    environment: str
    event_type: str
    occurred_at: datetime
    ingested_at: datetime
    severity: str
    message: str | None
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    metric_name: str | None
    metric_value: float | None
    duration_ms: float | None
    status_code: int | None
    attributes: dict
    ingestion_job_id: str | None
    event_hash: str


# ----------------------------------------------------------------------------- incidents

class IncidentOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    status: str
    severity: str
    environment: str
    affected_services: list[str]
    started_at: datetime
    ended_at: datetime | None
    created_at: datetime
    latest_analysis_run_id: uuid.UUID | None
    leading_hypothesis: str | None = None
    leading_cause_type: str | None = None
    analysis_status: str | None = None
    anomaly_count: int = 0


class IncidentPatch(BaseModel):
    status: Literal["open", "investigating", "resolved"]


class AnomalyOut(BaseModel):
    id: uuid.UUID
    service: str
    anomaly_type: str
    metric: str
    baseline: float
    observed: float
    score: float
    severity: str
    window_start: datetime
    window_end: datetime
    supporting_event_ids: list[str]
    incident_id: uuid.UUID | None
    details: dict


class TimelineEntryOut(BaseModel):
    at: datetime
    kind: str
    service: str
    title: str
    event_id: str | None
    anomaly_id: str | None


class EvidenceOut(BaseModel):
    event_id: uuid.UUID
    polarity: str
    explanation: str
    occurred_at: datetime
    service: str
    event_type: str
    severity: str
    message: str | None


class HypothesisOut(BaseModel):
    id: uuid.UUID
    rank: int
    cause_type: str
    service: str
    ranking_score: float
    score_components: dict
    summary: str
    uncertainty: str
    suggested_checks: list[str]
    evidence: list[EvidenceOut]


class AnalyzeIn(BaseModel):
    strategy: str = "hybrid"


class AnalysisRunOut(ORM):
    id: uuid.UUID
    incident_id: uuid.UUID
    algorithm_version: str
    model_version: str
    status: str
    progress: int
    stage: str
    config: dict
    summary: dict
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class HypothesesOut(BaseModel):
    analysis_run: AnalysisRunOut | None
    hypotheses: list[HypothesisOut]


class InvestigateIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class InvestigationOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    evidence: list[EvidenceOut]
    uncertainties: list[str]
    suggested_checks: list[str]
    insufficient_evidence: bool
    model_version: str
    writer: str
    fallback_reason: str | None = None
    citations_rejected: int = 0
    created_at: datetime


class FeedbackIn(BaseModel):
    verdict: Literal["correct", "partially_correct", "incorrect"]
    hypothesis_id: uuid.UUID | None = None
    analysis_run_id: uuid.UUID | None = None
    comment: str = Field(default="", max_length=2000)


class FeedbackOut(ORM):
    id: uuid.UUID
    incident_id: uuid.UUID
    analysis_run_id: uuid.UUID | None
    hypothesis_id: uuid.UUID | None
    verdict: str
    comment: str
    created_at: datetime


class ReportOut(ORM):
    id: uuid.UUID
    incident_id: uuid.UUID
    analysis_run_id: uuid.UUID
    algorithm_version: str
    model_version: str
    content_markdown: str
    created_at: datetime


# ----------------------------------------------------------------------------- evaluation

class EvaluationIn(BaseModel):
    seed: int = Field(default=42, ge=0, le=1_000_000)
    scenario_count: int = Field(default=20, ge=1, le=60)
    strategies: list[str] | None = None


class EvaluationRunOut(ORM):
    id: uuid.UUID
    algorithm_version: str
    status: str
    progress: int
    seed: int
    scenario_count: int
    config: dict
    metrics: dict
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class EvaluationResultOut(ORM):
    scenario_id: str
    strategy: str
    fault_type: str
    true_cause_service: str
    predicted_services: list[str]
    top1_correct: bool
    top3_correct: bool
    anomaly_tp: int
    anomaly_fp: int
    anomaly_fn: int
    detected: bool
    multi_fault: bool
    citations_total: int
    citations_invalid: int
    latency_ms: float
