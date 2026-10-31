from datetime import UTC, datetime

import pytest

from app.ingestion.normalize import RowError, normalize_record, parse_file, parse_timestamp


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-01-15T09:00:00Z", datetime(2026, 1, 15, 9, 0, tzinfo=UTC)),
        ("2026-01-15T11:00:00+02:00", datetime(2026, 1, 15, 9, 0, tzinfo=UTC)),
        (1768467600, datetime(2026, 1, 15, 9, 0, tzinfo=UTC)),  # seconds
        (1768467600000, datetime(2026, 1, 15, 9, 0, tzinfo=UTC)),  # milliseconds
        (1768467600000000000, datetime(2026, 1, 15, 9, 0, tzinfo=UTC)),  # nanoseconds
        ("1768467600", datetime(2026, 1, 15, 9, 0, tzinfo=UTC)),  # numeric string
    ],
)
def test_timestamp_formats_normalise_to_utc(value, expected):
    parsed, assumed = parse_timestamp(value)
    assert parsed == expected
    assert not assumed


def test_naive_timestamp_is_assumed_utc_and_flagged():
    parsed, assumed = parse_timestamp("2026-01-15 09:00:00")
    assert parsed == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    assert assumed


@pytest.mark.parametrize("value", ["", None, "yesterday", "1899-01-01T00:00:00Z", "2999-01-01T00:00:00Z", float("nan")])
def test_bad_timestamps_rejected(value):
    with pytest.raises(RowError):
        parse_timestamp(value)


def test_malformed_rows_are_reported_not_dropped():
    content = b"\n".join([
        b'{"timestamp":"2026-01-15T09:00:00Z","service":"api","message":"ok"}',
        b'{"timestamp":"2026-01-15T09:00:01Z","service":"api"',  # invalid JSON
        b'["not","an","object"]',
        b'{"service":"api","message":"no time"}',
        b'{"timestamp":"2026-01-15T09:00:02Z","message":"no service"}',
        b'{"timestamp":"2026-01-15T09:00:03Z","service":"api","type":"metric","metric":"cpu"}',  # missing value
        b'{"timestamp":"2026-01-15T09:00:04Z","service":"api","type":"span","span_id":"a1"}',  # missing duration
        b'{"timestamp":"2026-01-15T09:00:05Z","service":"api","type":"bogus"}',
        b'{"timestamp":"2026-01-15T09:00:06Z","service":"api","status_code":999}',
    ])
    result = parse_file(content, "x.jsonl")
    assert result.total_rows == 9
    assert len(result.records) == 1
    assert result.rejected_rows == 8
    rows = {e["row"] for e in result.errors}
    assert rows == {2, 3, 4, 5, 6, 7, 8, 9}
    assert all(e["error"] for e in result.errors)


def test_duplicates_within_file_are_counted():
    line = b'{"timestamp":"2026-01-15T09:00:00Z","service":"api","message":"boom","level":"error"}'
    result = parse_file(line + b"\n" + line + b"\n" + line, "dup.jsonl")
    assert len(result.records) == 1
    assert result.duplicate_rows == 2


def test_csv_with_attribute_columns_and_mapping():
    content = (
        b"time,service_name,level,msg,trace_id,attr.region,duration_ms,span_id,type\n"
        b"2026-01-15T09:00:00Z,Payments API,ERROR,card declined,abc123,eu-west,12.5,s1,span\n"
        b"2026-01-15T09:00:01Z,checkout,warn,slow,abc123,eu-west,,,log\n"
    )
    result = parse_file(content, "t.csv", mapping={"Payments API": "payments-service"})
    assert result.rejected_rows == 0, result.errors
    first, second = result.records
    assert first.service == "payments-service"
    assert first.event_type == "span" and first.duration_ms == 12.5 and first.severity == "error"
    assert first.attributes["region"] == "eu-west"
    assert second.severity == "warning"


def test_sensitive_values_are_redacted():
    rec = normalize_record({
        "timestamp": "2026-01-15T09:00:00Z", "service": "auth", "message": "login failed for jane@example.com token=abcd1234efgh",
        "attributes": {"password": "hunter2", "Authorization": "Bearer xyz", "user": {"api_key": "k"}},
    }, 1)
    assert "jane@example.com" not in rec.message and "abcd1234efgh" not in rec.message
    assert rec.attributes["password"] == "[REDACTED]"
    assert rec.attributes["Authorization"] == "[REDACTED]"
    assert rec.attributes["user"]["api_key"] == "[REDACTED]"


def test_event_hash_is_stable_and_content_sensitive():
    base = {"timestamp": "2026-01-15T09:00:00Z", "service": "api", "message": "x"}
    assert normalize_record(base, 1).event_hash == normalize_record(dict(base), 2).event_hash
    assert normalize_record(base, 1).event_hash != normalize_record({**base, "message": "y"}, 1).event_hash


def test_row_limit_rejects_excess_rows():
    lines = b"\n".join(b'{"timestamp":"2026-01-15T09:00:%02dZ","service":"api","message":"m"}' % i for i in range(5))
    result = parse_file(lines, "x.jsonl", max_rows=3)
    assert len(result.records) == 3 and result.rejected_rows == 2


def test_non_utf8_file_reported():
    result = parse_file(b"\xff\xfe\x00bad", "x.csv")
    assert result.rejected_rows == 1 and "UTF-8" in result.errors[0]["error"]
