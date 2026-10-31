"""Robust anomaly detection over telemetry-derived time series.

Method
------
For each service we derive bucketed series (request count, error rate, p95 latency and
every explicit metric). For each bucket we compute a rolling baseline from the previous
``baseline_buckets`` *non-anomalous* observations and a robust deviation score::

    score = (x - median) / (1.4826 * MAD)

MAD is floored with a metric-specific minimum scale so that flat (zero-variance)
baselines don't turn tiny fluctuations into infinite scores. A bucket is anomalous only
when *both* the robust score exceeds ``z_threshold`` *and* an absolute, human-meaningful
threshold is crossed (e.g. error rate >= 5 % and +4 points over baseline). Consecutive
anomalous buckets are merged into one anomaly window.

Separately, previously unseen error message signatures are reported as
``new_error_signature`` anomalies.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

import numpy as np
import pandas as pd

from app.analysis.types import AnalysisConfig, Anomaly, Event

MAD_TO_SIGMA = 1.4826
RESOURCE_HINTS = ("cpu", "memory", "mem_", "heap", "gc", "pool", "connection", "queue", "disk", "saturation", "thread")


def anomaly_fingerprint(service: str, anomaly_type: str, metric: str, start: datetime) -> str:
    raw = f"{service}|{anomaly_type}|{metric}|{start.isoformat()}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:20]


def events_frame(events: Iterable[Event], cfg: AnalysisConfig) -> pd.DataFrame:
    rows = [
        {
            "id": e.id,
            "service": e.service,
            "event_type": e.event_type,
            "occurred_at": e.occurred_at,
            "is_error": e.is_error,
            "duration_ms": e.duration_ms,
            "metric_name": e.metric_name,
            "metric_value": e.metric_value,
            "message": e.message,
        }
        for e in events
    ]
    if not rows:
        return pd.DataFrame(
            columns=["id", "service", "event_type", "occurred_at", "is_error", "duration_ms",
                     "metric_name", "metric_value", "message", "bucket"]
        )
    df = pd.DataFrame(rows)
    df["occurred_at"] = pd.to_datetime(df["occurred_at"], utc=True)
    df["bucket"] = df["occurred_at"].dt.floor(f"{cfg.bucket_seconds}s")
    return df


def build_series(df: pd.DataFrame, cfg: AnalysisConfig) -> dict[tuple[str, str], pd.Series]:
    """Return {(service, metric): series indexed by bucket}. Missing observations are NaN,
    except request counts where an empty bucket inside the observed range is a real zero."""
    series: dict[tuple[str, str], pd.Series] = {}
    if df.empty:
        return series
    full_index = pd.date_range(df["bucket"].min(), df["bucket"].max(), freq=f"{cfg.bucket_seconds}s")

    traffic = df[df["event_type"].isin(["span", "log"])]
    for service, sdf in traffic.groupby("service"):
        # Prefer spans as the request signal; fall back to logs for services without tracing.
        spans = sdf[sdf["event_type"] == "span"]
        base = spans if len(spans) else sdf
        grouped = base.groupby("bucket")
        counts = grouped.size().reindex(full_index)
        first, last = base["bucket"].min(), base["bucket"].max()
        active = (full_index >= first) & (full_index <= last)
        counts = counts.where(~(active & counts.isna()), 0.0).astype(float)
        errors = grouped["is_error"].sum().reindex(full_index).astype(float)
        # A rate needs enough requests to be defined. A high rate built from fewer than
        # ``min_error_count`` failures (1 error in 6 requests = "17 %") is not evidence
        # either way, so it is treated as a missing observation.
        rate = (errors / counts).where(counts >= cfg.min_requests_per_bucket)
        rate = rate.where((errors >= cfg.min_error_count) | (rate < cfg.min_error_rate))
        series[(service, "request_count")] = counts
        series[(service, "error_rate")] = rate
        if len(spans):
            p95 = spans.groupby("bucket")["duration_ms"].quantile(0.95).reindex(full_index)
            series[(service, "latency_p95_ms")] = p95.where(counts >= cfg.min_requests_per_bucket)

    metrics = df[(df["event_type"] == "metric") & df["metric_name"].notna()]
    for (service, name), mdf in metrics.groupby(["service", "metric_name"]):
        series[(service, f"metric:{name}")] = mdf.groupby("bucket")["metric_value"].mean().reindex(full_index)
    return series


def _min_scale(metric: str, median: float) -> float:
    if metric == "error_rate":
        return 0.01
    if metric == "latency_p95_ms":
        return max(5.0, 0.05 * abs(median))
    if metric == "request_count":
        return max(1.0, 0.1 * abs(median))
    return max(1e-6, 0.05 * abs(median))


def robust_score(value: float, baseline: np.ndarray, metric: str) -> tuple[float, float, float]:
    """Return (score, median, scale). Positive scores mean ``value`` is above baseline."""
    median = float(np.median(baseline))
    mad = float(np.median(np.abs(baseline - median)))
    scale = max(MAD_TO_SIGMA * mad, _min_scale(metric, median))
    return (value - median) / scale, median, scale


def _absolute_check(metric: str, value: float, median: float, cfg: AnalysisConfig) -> str | None:
    """Return an anomaly type if the absolute threshold for this metric is crossed."""
    if metric == "error_rate":
        if value >= cfg.min_error_rate and value - median >= cfg.min_error_rate_delta:
            return "error_rate_spike"
        return None
    if metric == "latency_p95_ms":
        if value - median >= cfg.min_latency_delta_ms and value >= cfg.min_latency_ratio * max(median, 1.0):
            return "latency_spike"
        return None
    if metric == "request_count":
        if median >= 2 * cfg.min_requests_per_bucket and value <= 0.5 * median:
            return "traffic_drop"
        if median >= cfg.min_requests_per_bucket and value >= 2.5 * median + 5:
            return "traffic_surge"
        return None
    if abs(value - median) >= cfg.metric_min_relative_change * max(abs(median), 1e-6):
        return "metric_shift"
    return None


def detect_in_series(
    series: pd.Series, metric: str, cfg: AnalysisConfig
) -> tuple[list[dict], dict]:
    """Scan one series. Returns (flagged buckets, stats) where each flagged bucket is
    {time, value, median, score, type}. Anomalous points are excluded from later
    baselines so a long incident does not become its own baseline."""
    values = series.to_numpy(dtype=float)
    times = series.index
    clean: list[float] = []
    flagged: list[dict] = []
    stats = {"evaluated": 0, "insufficient_baseline": 0, "missing": 0}
    two_sided = metric == "request_count" or metric.startswith("metric:")
    for i, value in enumerate(values):
        if np.isnan(value):
            stats["missing"] += 1
            continue
        window = np.asarray(clean[-cfg.baseline_buckets:], dtype=float)
        if len(window) < cfg.min_baseline_points:
            stats["insufficient_baseline"] += 1
            clean.append(value)
            continue
        stats["evaluated"] += 1
        score, median, _ = robust_score(value, window, metric)
        effective = abs(score) if two_sided else score
        anomaly_type = _absolute_check(metric, value, median, cfg) if effective >= cfg.z_threshold else None
        if anomaly_type:
            flagged.append(
                {"time": times[i].to_pydatetime(), "value": value, "median": median,
                 "score": float(effective), "type": anomaly_type}
            )
        else:
            clean.append(value)
    return flagged, stats


def _merge_windows(flagged: list[dict], cfg: AnalysisConfig) -> list[list[dict]]:
    groups: list[list[dict]] = []
    gap = cfg.bucket * (cfg.merge_gap_buckets + 1)
    for point in flagged:
        if groups and groups[-1][-1]["type"] == point["type"] and point["time"] - groups[-1][-1]["time"] <= gap:
            groups[-1].append(point)
        else:
            groups.append([point])
    return groups


def severity_for(anomaly_type: str, score: float, observed: float, baseline: float) -> str:
    if anomaly_type == "error_rate_spike":
        if observed >= 0.5:
            return "critical"
        if observed >= 0.2:
            return "high"
        return "medium" if observed >= 0.08 else "low"
    ratio = observed / baseline if baseline > 0 else float("inf")
    if anomaly_type == "latency_spike":
        if ratio >= 5:
            return "critical"
        return "high" if ratio >= 2.5 else "medium"
    if anomaly_type == "traffic_drop":
        return "high" if observed <= 0.1 * baseline else "medium"
    if score >= 20:
        return "high"
    return "medium" if score >= 8 else "low"


def _supporting_events(df: pd.DataFrame, service: str, metric: str, start: datetime, end: datetime, cap: int = 20) -> list[str]:
    window = df[(df["service"] == service) & (df["occurred_at"] >= start) & (df["occurred_at"] < end)]
    if metric == "error_rate":
        picked = window[window["is_error"]].sort_values("occurred_at")
    elif metric == "latency_p95_ms":
        picked = window[window["event_type"] == "span"].sort_values("duration_ms", ascending=False)
    elif metric.startswith("metric:"):
        picked = window[(window["event_type"] == "metric") & (window["metric_name"] == metric[7:])].sort_values("occurred_at")
    else:
        picked = window[window["event_type"].isin(["span", "log"])].sort_values("occurred_at")
    return picked["id"].head(cap).tolist()


_SIGNATURE_PATTERNS = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<id>"),
    (re.compile(r"\b\d+(\.\d+)?(ms|s|%)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def message_signature(message: str) -> str:
    sig = message.strip().lower()[:300]
    for pattern, repl in _SIGNATURE_PATTERNS:
        sig = pattern.sub(repl, sig)
    return sig.strip()


def detect_new_error_signatures(df: pd.DataFrame, cfg: AnalysisConfig) -> list[Anomaly]:
    errors = df[df["is_error"] & df["message"].notna()].sort_values("occurred_at")
    if errors.empty:
        return []
    data_start = df["occurred_at"].min()
    learning_until = data_start + cfg.bucket * cfg.min_baseline_points
    by_key: dict[tuple[str, str], list] = defaultdict(list)
    for row in errors.itertuples(index=False):
        by_key[(row.service, message_signature(row.message))].append(row)
    anomalies = []
    burst = pd.Timedelta(minutes=cfg.new_signature_burst_minutes)
    need = cfg.new_signature_min_count
    for (service, signature), rows in by_key.items():
        # Signatures present during the learning period are part of the baseline.
        if rows[0].occurred_at < learning_until or len(rows) < need:
            continue
        # The anomaly starts at the first *burst* (``need`` occurrences within ``burst``),
        # so a stray earlier occurrence does not drag the window start backwards.
        first_idx = next(
            (i for i in range(len(rows) - need + 1) if rows[i + need - 1].occurred_at - rows[i].occurred_at <= burst),
            None,
        )
        if first_idx is None:
            continue
        # Onset is the occurrence that completes the burst: an isolated earlier occurrence
        # (e.g. one background failure two minutes before the real fault) is not the onset.
        # The anomaly ends with that first burst; once seen, later recurrences of the same
        # signature are no longer "new" (they are covered by error-rate detection).
        rows = rows[first_idx:]
        end_idx = next((i for i in range(1, len(rows)) if rows[i].occurred_at - rows[i - 1].occurred_at > burst), len(rows))
        rows = rows[:end_idx]
        start = rows[need - 1].occurred_at.floor(f"{cfg.bucket_seconds}s").to_pydatetime()
        end = rows[-1].occurred_at.to_pydatetime() + cfg.bucket
        count = len(rows)
        anomalies.append(
            Anomaly(
                id=anomaly_fingerprint(service, "new_error_signature", signature[:60], start),
                service=service,
                anomaly_type="new_error_signature",
                metric="error_signature",
                baseline=0.0,
                observed=float(count),
                score=float(min(50.0, 4.0 + count)),
                severity="high" if count >= 20 else "medium",
                window_start=start,
                window_end=end,
                supporting_event_ids=[r.id for r in rows[:20]],
                details={"signature": signature, "example": rows[0].message[:300], "count": count},
            )
        )
    return anomalies


def detect_anomalies(events: Iterable[Event], cfg: AnalysisConfig | None = None) -> tuple[list[Anomaly], dict]:
    """Detect anomalies across all services. Returns (anomalies, diagnostics)."""
    cfg = cfg or AnalysisConfig()
    df = events_frame(events, cfg)
    diagnostics = {"series": 0, "evaluated_points": 0, "insufficient_baseline_points": 0, "missing_points": 0}
    if df.empty:
        diagnostics["reason"] = "no events"
        return [], diagnostics

    anomalies: list[Anomaly] = []
    for (service, metric), series in build_series(df, cfg).items():
        diagnostics["series"] += 1
        flagged, stats = detect_in_series(series, metric, cfg)
        diagnostics["evaluated_points"] += stats["evaluated"]
        diagnostics["insufficient_baseline_points"] += stats["insufficient_baseline"]
        diagnostics["missing_points"] += stats["missing"]
        for group in _merge_windows(flagged, cfg):
            start = group[0]["time"]
            end = group[-1]["time"] + cfg.bucket
            peak = max(group, key=lambda p: p["score"])
            baseline = group[0]["median"]
            anomaly_type = group[0]["type"]
            anomalies.append(
                Anomaly(
                    id=anomaly_fingerprint(service, anomaly_type, metric, start),
                    service=service,
                    anomaly_type=anomaly_type,
                    metric=metric,
                    baseline=round(float(baseline), 6),
                    observed=round(float(peak["value"]), 6),
                    score=round(float(peak["score"]), 3),
                    severity=severity_for(anomaly_type, peak["score"], peak["value"], baseline),
                    window_start=start,
                    window_end=end,
                    supporting_event_ids=_supporting_events(df, service, metric, start, end),
                    details={
                        "buckets": len(group),
                        "bucket_seconds": cfg.bucket_seconds,
                        "resource_metric": metric.startswith("metric:")
                        and any(h in metric.lower() for h in RESOURCE_HINTS),
                    },
                )
            )
    anomalies.extend(detect_new_error_signatures(df, cfg))
    anomalies.sort(key=lambda a: (a.window_start, a.service, a.metric))
    return anomalies, diagnostics
