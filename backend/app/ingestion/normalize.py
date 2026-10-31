"""Parsing, validation and normalisation of uploaded telemetry (JSONL and CSV).

Every input row ends up either *accepted* (as a normalised record) or *rejected* with a
row number and reason. Nothing is dropped silently. Accepted records are hashed so the
same event uploaded twice is recognised as a duplicate.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

EVENT_TYPES = ("log", "span", "metric", "deployment", "dependency")
MAX_MESSAGE_CHARS = 4000
MAX_ATTRIBUTES_BYTES = 16_384
MAX_ROW_BYTES = 64_000
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._\-/]{0,159}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")

FIELD_ALIASES = {
    "timestamp": ("timestamp", "time", "ts", "@timestamp", "occurred_at", "datetime"),
    "service": ("service", "service_name", "service.name", "app", "application"),
    "event_type": ("type", "event_type", "kind"),
    "severity": ("severity", "level", "log_level", "loglevel"),
    "message": ("message", "msg", "log", "body"),
    "trace_id": ("trace_id", "traceid", "trace.id", "traceId"),
    "span_id": ("span_id", "spanid", "span.id", "spanId"),
    "parent_span_id": ("parent_span_id", "parentspanid", "parent_id", "parentSpanId"),
    "duration_ms": ("duration_ms", "duration", "latency_ms"),
    "status_code": ("status_code", "http.status_code", "http_status", "statusCode"),
    "metric_name": ("metric", "metric_name"),
    "metric_value": ("value", "metric_value"),
    "environment": ("environment", "env", "deployment.environment"),
    "attributes": ("attributes", "attrs", "tags", "labels"),
}
_ALIAS_LOOKUP = {alias.lower(): canonical for canonical, aliases in FIELD_ALIASES.items() for alias in aliases}

SEVERITY_MAP = {
    "trace": "debug", "debug": "debug", "info": "info", "information": "info", "notice": "info",
    "warn": "warning", "warning": "warning", "err": "error", "error": "error",
    "crit": "critical", "critical": "critical", "fatal": "fatal", "alert": "critical", "emerg": "fatal",
}

SENSITIVE_KEY_RE = re.compile(r"pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|session|ssn|credit[_-]?card|card[_-]?number", re.I)
_REDACTIONS = [
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED-NUMBER]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED-EMAIL]"),
]
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class RowError(ValueError):
    pass


@dataclass(slots=True)
class NormalizedRecord:
    row: int
    service: str
    environment: str
    event_type: str
    occurred_at: datetime
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
    event_hash: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_preview(self) -> dict:
        return {
            "row": self.row,
            "service": self.service,
            "environment": self.environment,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "severity": self.severity,
            "message": self.message,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "duration_ms": self.duration_ms,
            "status_code": self.status_code,
            "attributes": self.attributes,
            "event_hash": self.event_hash,
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class ParseResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    total_rows: int = 0
    rejected_rows: int = 0
    duplicate_rows: int = 0
    columns: dict[str, int] = field(default_factory=dict)
    warnings: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------- helpers

def redact_text(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def redact_attributes(value):
    if isinstance(value, dict):
        return {
            str(k)[:128]: ("[REDACTED]" if SENSITIVE_KEY_RE.search(str(k)) else redact_attributes(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_attributes(v) for v in value[:100]]
    if isinstance(value, str):
        return redact_text(_CONTROL_RE.sub("", value))[:2000]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def parse_timestamp(value, now: datetime | None = None) -> tuple[datetime, bool]:
    """Return (UTC datetime, assumed_utc). Accepts ISO-8601 strings, common log formats and
    epoch numbers in s / ms / µs / ns (magnitude-detected)."""
    now = now or datetime.now(UTC)
    assumed = False
    if isinstance(value, bool) or value is None or value == "":
        raise RowError("missing timestamp")
    if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
        value = float(value)
    if isinstance(value, int | float):
        number = float(value)
        if not math.isfinite(number):
            raise RowError("timestamp is not finite")
        magnitude = abs(number)
        divisor = 1e9 if magnitude > 1e17 else 1e6 if magnitude > 1e14 else 1e3 if magnitude > 1e11 else 1.0
        try:
            parsed = datetime.fromtimestamp(number / divisor, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise RowError("timestamp out of range") from exc
    elif isinstance(value, str):
        text = value.strip()
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00") if text.endswith("Z") else text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y/%m/%d %H:%M:%S", "%d/%b/%Y:%H:%M:%S %z", "%b %d %Y %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise RowError(f"unparseable timestamp {text[:40]!r}")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
            assumed = True
        parsed = parsed.astimezone(UTC)
    else:
        raise RowError("timestamp must be a string or number")
    if parsed.year < 2000 or parsed > now + timedelta(days=1):
        raise RowError(f"timestamp {parsed.isoformat()} outside accepted range")
    return parsed, assumed


def canonical_service(raw, mapping: dict[str, str]) -> str:
    if raw is None or str(raw).strip() == "":
        raise RowError("missing service")
    name = str(raw).strip()
    if name in mapping:
        name = mapping[name]
    name = re.sub(r"\s+", "-", name.strip().lower())
    if name in mapping:
        name = mapping[name]
    if not SERVICE_RE.match(name):
        raise RowError(f"invalid service name {str(raw)[:40]!r}")
    return name


def _float(value, name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RowError(f"{name} is not numeric") from exc
    if not math.isfinite(number):
        raise RowError(f"{name} is not finite")
    return number


def _ident(value, name: str) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not ID_RE.match(text):
        raise RowError(f"invalid {name}")
    return text


def event_hash(rec: NormalizedRecord) -> str:
    canonical = json.dumps(
        [rec.service, rec.environment, rec.event_type, rec.occurred_at.isoformat(), rec.severity, rec.message,
         rec.trace_id, rec.span_id, rec.parent_span_id, rec.metric_name, rec.metric_value, rec.duration_ms,
         rec.status_code, rec.attributes],
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def normalize_record(raw: dict, row: int, mapping: dict[str, str] | None = None,
                     default_environment: str = "production", now: datetime | None = None) -> NormalizedRecord:
    if not isinstance(raw, dict):
        raise RowError("row is not an object")
    mapping = mapping or {}
    fields: dict = {}
    extra: dict = {}
    for key, value in raw.items():
        key_s = str(key)
        canonical = _ALIAS_LOOKUP.get(key_s.lower())
        if canonical and canonical not in fields:
            fields[canonical] = value
        elif key_s.lower().startswith("attr."):
            extra[key_s[5:]] = value
        else:
            extra[key_s] = value

    attributes = fields.get("attributes") or {}
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes) if attributes.strip() else {}
        except json.JSONDecodeError as exc:
            raise RowError("attributes is not valid JSON") from exc
    if not isinstance(attributes, dict):
        raise RowError("attributes must be an object")
    attributes = {**{k: v for k, v in extra.items() if v not in (None, "")}, **attributes}

    occurred_at, assumed_utc = parse_timestamp(fields.get("timestamp"), now)
    service = canonical_service(fields.get("service"), mapping)
    environment = str(fields.get("environment") or default_environment).strip().lower()[:64] or default_environment

    duration = _float(fields.get("duration_ms"), "duration_ms")
    metric_value = _float(fields.get("metric_value"), "value")
    metric_name = str(fields["metric_name"]).strip()[:128] if fields.get("metric_name") not in (None, "") else None
    status_raw = fields.get("status_code")
    status_code = None
    if status_raw not in (None, ""):
        status_float = _float(status_raw, "status_code")
        if status_float is None or not (100 <= status_float <= 599):
            raise RowError("status_code must be between 100 and 599")
        status_code = int(status_float)

    event_type = str(fields.get("event_type") or "").strip().lower()
    if not event_type:
        if metric_name and metric_value is not None:
            event_type = "metric"
        elif fields.get("span_id") and duration is not None:
            event_type = "span"
        else:
            event_type = "log"
    if event_type in ("trace", "spans"):
        event_type = "span"
    if event_type in ("deploy", "release", "change"):
        event_type = "deployment"
    if event_type not in EVENT_TYPES:
        raise RowError(f"unknown event type {event_type[:20]!r}")

    warnings: list[str] = []
    if assumed_utc:
        warnings.append("timestamp had no timezone; assumed UTC")
    severity_raw = str(fields.get("severity") or "").strip().lower()
    severity = SEVERITY_MAP.get(severity_raw, "info")
    if severity_raw and severity_raw not in SEVERITY_MAP:
        attributes["original_severity"] = severity_raw[:32]
        warnings.append("unknown severity mapped to info")
    if event_type == "span" and severity == "info" and status_code and status_code >= 500:
        severity = "error"

    if event_type == "metric" and (metric_name is None or metric_value is None):
        raise RowError("metric events require metric name and numeric value")
    if event_type == "span":
        if duration is None:
            raise RowError("span events require duration_ms")
        if not fields.get("span_id"):
            raise RowError("span events require span_id")
    if duration is not None and duration < 0:
        raise RowError("duration_ms must be non-negative")
    if event_type == "dependency" and not (attributes.get("target") or attributes.get("depends_on")):
        raise RowError("dependency events require a target service")
    if event_type == "dependency":
        target = attributes.get("target") or attributes.get("depends_on")
        attributes["target"] = canonical_service(target, mapping)
        attributes.pop("depends_on", None)

    message = fields.get("message")
    if message is not None:
        message = _CONTROL_RE.sub("", str(message))
        if len(message) > MAX_MESSAGE_CHARS:
            message = message[:MAX_MESSAGE_CHARS]
            warnings.append("message truncated")
        message = redact_text(message) or None

    attributes = redact_attributes(attributes)
    if len(json.dumps(attributes, default=str)) > MAX_ATTRIBUTES_BYTES:
        raise RowError("attributes exceed 16 KB")

    record = NormalizedRecord(
        row=row, service=service, environment=environment, event_type=event_type, occurred_at=occurred_at,
        severity=severity, message=message, trace_id=_ident(fields.get("trace_id"), "trace_id"),
        span_id=_ident(fields.get("span_id"), "span_id"), parent_span_id=_ident(fields.get("parent_span_id"), "parent_span_id"),
        metric_name=metric_name, metric_value=metric_value, duration_ms=duration, status_code=status_code,
        attributes=attributes, warnings=warnings,
    )
    record.event_hash = event_hash(record)
    return record


# ---------------------------------------------------------------------------- file readers

def detect_format(filename: str, head: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".jsonl", ".ndjson", ".json")):
        return "jsonl"
    if lower.endswith((".csv", ".tsv")):
        return "csv"
    stripped = head.lstrip()
    return "jsonl" if stripped.startswith(b"{") else "csv"


def iter_rows(content: bytes, file_format: str) -> Iterator[tuple[int, dict | None, str | None]]:
    """Yield (row_number, raw_dict | None, error | None). Row numbers are 1-based lines
    (JSONL) or data rows after the header (CSV)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        yield 0, None, "file is not valid UTF-8"
        return
    if file_format == "jsonl":
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            if len(line) > MAX_ROW_BYTES:
                yield number, None, "row exceeds 64 KB"
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                yield number, None, f"invalid JSON: {exc.msg}"
                continue
            if not isinstance(value, dict):
                yield number, None, "row is not a JSON object"
                continue
            yield number, value, None
        return
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        yield 0, None, "CSV has no header row"
        return
    for number, row in enumerate(reader, start=1):
        if None in row:
            yield number, None, "row has more columns than the header"
            continue
        yield number, {k: v for k, v in row.items() if k is not None}, None


def parse_file(content: bytes, filename: str, mapping: dict[str, str] | None = None,
               environment: str = "production", max_rows: int = 250_000, limit: int | None = None,
               max_errors_kept: int = 500) -> ParseResult:
    result = ParseResult()
    file_format = detect_format(filename, content[:256])
    seen_hashes: set[str] = set()
    now = datetime.now(UTC)
    for number, raw, error in iter_rows(content, file_format):
        if limit is not None and result.total_rows >= limit:
            break
        result.total_rows += 1
        if result.total_rows > max_rows:
            result.rejected_rows += 1
            if len(result.errors) < max_errors_kept:
                result.errors.append({"row": number, "error": f"exceeds the {max_rows}-row limit"})
            continue
        if raw is not None:
            for key in raw:
                result.columns[str(key)] = result.columns.get(str(key), 0) + 1
        if error is None:
            try:
                record = normalize_record(raw, number, mapping, environment, now)  # type: ignore[arg-type]
            except RowError as exc:
                error = str(exc)
        if error is not None:
            result.rejected_rows += 1
            if len(result.errors) < max_errors_kept:
                result.errors.append({"row": number, "error": error})
            continue
        if record.event_hash in seen_hashes:
            result.duplicate_rows += 1
            continue
        seen_hashes.add(record.event_hash)
        for warning in record.warnings:
            result.warnings[warning] = result.warnings.get(warning, 0) + 1
        result.records.append(record)
    return result
