"""Plain domain types shared by the analysis pipeline.

The pipeline never touches the database: callers convert ORM rows into ``Event`` objects.
That keeps every algorithm unit-testable and lets the benchmark run fully in memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

ALGORITHM_VERSION = "tm-rca-1.0.0"

ERROR_SEVERITIES = {"error", "critical", "fatal"}


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    service: str
    event_type: str  # log | span | metric | deployment | dependency
    occurred_at: datetime
    severity: str = "info"
    message: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None
    duration_ms: float | None = None
    status_code: int | None = None
    attributes: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def is_error(self) -> bool:
        if self.event_type not in ("log", "span"):
            return False
        if self.severity in ERROR_SEVERITIES:
            return True
        if self.status_code is not None and self.status_code >= 500:
            return True
        return str(self.attributes.get("status", "")).lower() == "error"


@dataclass(slots=True)
class AnalysisConfig:
    bucket_seconds: int = 60
    baseline_buckets: int = 30
    min_baseline_points: int = 10
    z_threshold: float = 4.0
    min_requests_per_bucket: int = 3
    min_error_rate: float = 0.05
    min_error_count: int = 3
    min_error_rate_delta: float = 0.04
    min_latency_delta_ms: float = 80.0
    min_latency_ratio: float = 1.4
    metric_min_relative_change: float = 0.4
    new_signature_min_count: int = 3
    new_signature_burst_minutes: float = 3.0
    merge_gap_buckets: int = 2
    correlation_gap_minutes: float = 5.0
    correlation_max_hops: int = 3
    deployment_lookback_minutes: float = 30.0
    temporal_tau_minutes: float = 4.0
    max_evidence_per_polarity: int = 8

    @property
    def bucket(self) -> timedelta:
        return timedelta(seconds=self.bucket_seconds)

    @classmethod
    def from_dict(cls, data: dict | None) -> AnalysisConfig:
        cfg = cls()
        for key, value in (data or {}).items():
            if hasattr(cfg, key) and isinstance(value, int | float) and not isinstance(value, bool):
                setattr(cfg, key, type(getattr(cfg, key))(value))
        return cfg

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


@dataclass(slots=True)
class Anomaly:
    id: str
    service: str
    anomaly_type: str  # error_rate_spike | latency_spike | traffic_drop | traffic_surge | metric_shift | new_error_signature
    metric: str
    baseline: float
    observed: float
    score: float
    severity: str  # critical | high | medium | low
    window_start: datetime
    window_end: datetime
    supporting_event_ids: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass(slots=True)
class GraphEdge:
    source: str  # caller
    target: str  # callee
    call_count: int = 0
    error_count: int = 0
    durations: list[float] = field(default_factory=list)
    evidence_event_ids: list[str] = field(default_factory=list)
    origins: set[str] = field(default_factory=set)  # span | explicit

    @property
    def p95_ms(self) -> float | None:
        if not self.durations:
            return None
        ordered = sorted(self.durations)
        return ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1) + 0.5))]


@dataclass(slots=True)
class TimelineEntry:
    at: datetime
    kind: str  # deployment | anomaly_start | first_error | error_signature | anomaly_end
    service: str
    title: str
    event_id: str | None = None
    anomaly_id: str | None = None


@dataclass(slots=True)
class CandidateIncident:
    fingerprint: str
    title: str
    severity: str
    started_at: datetime
    ended_at: datetime
    services: list[str]
    anomalies: list[Anomaly]
    deployment_event_ids: list[str]
    timeline: list[TimelineEntry]


@dataclass(slots=True)
class EvidenceRef:
    event_id: str
    polarity: str  # supporting | contradicting
    explanation: str


@dataclass(slots=True)
class RankedHypothesis:
    rank: int
    service: str
    cause_type: str
    ranking_score: float
    components: dict[str, float]
    summary: str
    uncertainty: str
    suggested_checks: list[str]
    evidence: list[EvidenceRef]
