"""Deterministic synthetic telemetry with injected faults and known ground truth.

A scenario simulates traffic through a 14-service e-commerce topology ("ShopFlow"):
distributed traces with parent/child spans, logs, per-minute resource metrics and
deployment events. Faults are injected into a chosen service and propagate to callers
through the call tree. The generator records, per fault, which services were *actually*
affected (share of spans influenced by the fault) so anomaly detection can be scored.

Everything is driven by a seeded ``numpy.random.Generator``: the same seed always yields
byte-identical output.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

from app.analysis.types import Event

FAULT_TYPES = ("bad_deployment", "latency_injection", "resource_exhaustion", "dependency_down")


@dataclass(frozen=True)
class CallNode:
    service: str
    operation: str
    children: tuple[CallNode, ...] = ()


def _n(service: str, op: str, *children: CallNode) -> CallNode:
    return CallNode(service, op, tuple(children))


ROUTES: tuple[tuple[str, float, CallNode], ...] = (
    ("browse", 0.5, _n("web-frontend", "GET /products",
        _n("api-gateway", "GET /v1/products",
           _n("auth-service", "verify_token"),
           _n("catalog-service", "list_products",
              _n("search-service", "query"),
              _n("catalog-db", "SELECT products"))))),
    ("checkout", 0.25, _n("web-frontend", "POST /checkout",
        _n("api-gateway", "POST /v1/checkout",
           _n("auth-service", "verify_token"),
           _n("checkout-service", "create_order",
              _n("inventory-service", "reserve_stock", _n("inventory-db", "UPDATE stock")),
              _n("payments-service", "charge", _n("fraud-service", "score"), _n("payments-db", "INSERT payment")),
              _n("orders-db", "INSERT order"))))),
    ("account", 0.25, _n("web-frontend", "GET /account",
        _n("api-gateway", "GET /v1/account",
           _n("auth-service", "get_profile", _n("users-db", "SELECT user"))))),
)

BASE_LATENCY_MS = {
    "web-frontend": 15, "api-gateway": 5, "auth-service": 8, "users-db": 3, "catalog-service": 20,
    "search-service": 35, "catalog-db": 4, "checkout-service": 25, "inventory-service": 12,
    "inventory-db": 4, "payments-service": 30, "fraud-service": 20, "payments-db": 5, "orders-db": 5,
}
SERVICES = tuple(BASE_LATENCY_MS)
DATABASES = {s for s in SERVICES if s.endswith("-db")}
FAULTABLE = tuple(s for s in SERVICES if s not in ("web-frontend",))

BACKGROUND_ERRORS = (
    "upstream connection reset by peer",
    "request cancelled by client",
    "retrying request after transient error",
)
FAULT_MESSAGES = {
    "bad_deployment": (
        "NullPointerException in {op}: field 'currency' is null",
        "KeyError: 'region' while loading {service} config",
        "schema validation failed in {op}: unexpected field 'discount_v2'",
    ),
    "latency_injection": ("timeout waiting for {service} response after 2000ms",),
    "resource_exhaustion": ("connection pool exhausted: timed out waiting for connection (pool size 20)",),
    "dependency_down": ("connect ECONNREFUSED {service}:5432", "{service} unavailable: connection refused"),
}


@dataclass
class Fault:
    service: str
    fault_type: str
    start: datetime
    end: datetime
    severity: float  # 0..1 intensity
    message: str = ""
    affected_services: list[str] = field(default_factory=list)

    def active(self, t: datetime) -> bool:
        return self.start <= t < self.end


@dataclass
class Scenario:
    scenario_id: str
    seed: int
    start: datetime
    minutes: int
    faults: list[Fault]
    noise_windows: list[dict]
    records: list[dict]
    description: str

    def to_events(self) -> list[Event]:
        return records_to_events(self.records, prefix=self.scenario_id)

    def ground_truth(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "faults": [
                {"service": f.service, "fault_type": f.fault_type, "start": f.start.isoformat(),
                 "end": f.end.isoformat(), "affected_services": f.affected_services}
                for f in self.faults
            ],
            "noise_windows": self.noise_windows,
        }


def records_to_events(records: list[dict], prefix: str = "e") -> list[Event]:
    """Fast in-memory conversion of generator records (already canonical) to Events."""
    events = []
    for i, r in enumerate(records):
        attrs = dict(r.get("attributes") or {})
        events.append(
            Event(
                id=f"{prefix}:{i}",
                service=r["service"],
                event_type=r["type"],
                occurred_at=datetime.fromisoformat(r["timestamp"]),
                severity=r.get("severity", "info"),
                message=r.get("message"),
                trace_id=r.get("trace_id"),
                span_id=r.get("span_id"),
                parent_span_id=r.get("parent_span_id"),
                metric_name=r.get("metric"),
                metric_value=r.get("value"),
                duration_ms=r.get("duration_ms"),
                status_code=r.get("status_code"),
                attributes=attrs,
            )
        )
    return events


class _Sim:
    def __init__(self, rng: np.random.Generator, faults: list[Fault], noise: list[dict], missing_trace_fraction: float):
        self.rng = rng
        self.faults = faults
        self.noise = noise
        self.missing_trace_fraction = missing_trace_fraction
        self.records: list[dict] = []
        # (fault index, service) -> [influenced spans, total spans in window]
        self.influence: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0])

    def hex(self, n: int) -> str:
        return "".join(f"{b:02x}" for b in self.rng.integers(0, 256, n // 2, dtype=np.uint8))

    def _fault_for(self, service: str, t: datetime) -> tuple[int, Fault] | None:
        for idx, f in enumerate(self.faults):
            if f.service == service and f.active(t):
                return idx, f
        return None

    def span(self, node: CallNode, t: datetime, trace_id: str, parent: str | None) -> tuple[float, bool, int | None, set[int]]:
        """Simulate one span (recursively). Returns (duration, failed, status, fault indexes influencing it)."""
        rng = self.rng
        span_id = self.hex(16)
        own = float(BASE_LATENCY_MS[node.service] * rng.lognormal(0, 0.3))
        failed, status, message = False, None, None
        influenced: set[int] = set()
        hit = self._fault_for(node.service, t)
        if hit:
            idx, f = hit
            progress = min(1.0, (t - f.start).total_seconds() / 120.0)  # 2-minute ramp
            if f.fault_type == "bad_deployment":
                own += 10
                if rng.random() < 0.25 + 0.35 * f.severity:
                    failed, status, message = True, 500, f.message
            elif f.fault_type == "latency_injection":
                own += (400 + 900 * f.severity) * rng.lognormal(0, 0.2)
                if rng.random() < 0.05:
                    failed, status, message = True, 504, f.message
            elif f.fault_type == "resource_exhaustion":
                own += 250 * progress * rng.lognormal(0, 0.3)
                if progress >= 0.5 and rng.random() < (0.2 + 0.3 * f.severity) * progress:
                    failed, status, message = True, 503, f.message
            elif f.fault_type == "dependency_down":
                own = min(own, 3.0)
                if rng.random() < 0.6 + 0.3 * f.severity:
                    failed, status, message = True, 503, f.message
            influenced.add(idx)
        for w in self.noise:
            if w["service"] == node.service and w["start_dt"] <= t < w["end_dt"] and rng.random() < 0.18:
                failed, status, message = True, 500, w["message"]
        if not failed and rng.random() < 0.002:
            failed, status, message = True, 500, str(rng.choice(BACKGROUND_ERRORS))

        cursor = own * 0.3
        child_failed = None
        for child in node.children:
            child_t = t + timedelta(milliseconds=cursor)
            duration, c_failed, c_status, c_infl = self.span(child, child_t, trace_id, span_id)
            cursor += duration
            if c_infl:
                influenced |= c_infl
            if c_failed and not failed:
                if rng.random() < 0.85:
                    failed, status = True, 502 if c_status in (502, 503, 504) else 500
                    message = f"upstream {child.service} failed with status {c_status}"
                    child_failed = child.service
                else:
                    cursor += 50  # retried successfully
        duration = cursor + own * 0.7

        for idx, f in enumerate(self.faults):
            if f.active(t):
                counter = self.influence[(idx, node.service)]
                counter[1] += 1
                if idx in influenced and (failed or duration > 1.5 * BASE_LATENCY_MS[node.service] + 150):
                    counter[0] += 1

        drop_trace = rng.random() < self.missing_trace_fraction
        record = {
            "timestamp": t.isoformat(),
            "service": node.service,
            "type": "span",
            "trace_id": trace_id,
            "span_id": span_id,
            "duration_ms": round(duration, 3),
            "status_code": status or 200,
            "severity": "error" if failed else "info",
            "attributes": {"operation": node.operation},
        }
        if parent:
            record["parent_span_id"] = parent
        self.records.append(record)
        if failed:
            log = {
                "timestamp": (t + timedelta(milliseconds=duration)).isoformat(),
                "service": node.service,
                "type": "log",
                "severity": "error",
                "message": (message or "request failed").format(service=node.service, op=node.operation),
                "attributes": {"operation": node.operation, **({"upstream": child_failed} if child_failed else {})},
            }
            if not drop_trace:
                log["trace_id"] = trace_id
                log["span_id"] = span_id
            self.records.append(log)
        elif duration > 1000 and rng.random() < 0.3:
            self.records.append({
                "timestamp": (t + timedelta(milliseconds=duration)).isoformat(),
                "service": node.service, "type": "log", "severity": "warning",
                "message": f"slow request: {node.operation} took {duration:.0f}ms",
                **({} if drop_trace else {"trace_id": trace_id, "span_id": span_id}),
            })
        return duration, failed, status, influenced


def _route_choice(rng: np.random.Generator) -> CallNode:
    weights = np.array([w for _, w, _ in ROUTES])
    return ROUTES[int(rng.choice(len(ROUTES), p=weights / weights.sum()))][2]


def _version(rng: np.random.Generator) -> str:
    return f"v{rng.integers(1, 4)}.{rng.integers(0, 30)}.{rng.integers(0, 10)}"


def simulate(
    seed: int,
    faults: list[Fault],
    start: datetime,
    minutes: int,
    requests_per_minute: float = 30.0,
    missing_trace_fraction: float = 0.0,
    noise_windows: list[dict] | None = None,
    decoy_deployments: list[tuple[str, datetime]] | None = None,
    scenario_id: str = "scenario",
    description: str = "",
) -> Scenario:
    rng = np.random.default_rng(seed)
    noise = [
        {**w, "start_dt": datetime.fromisoformat(w["start"]), "end_dt": datetime.fromisoformat(w["end"])}
        for w in (noise_windows or [])
    ]
    sim = _Sim(rng, faults, noise, missing_trace_fraction)
    records = sim.records

    # explicit dependency declaration for an external system without telemetry
    records.append({
        "timestamp": start.isoformat(), "service": "payments-service", "type": "dependency",
        "attributes": {"target": "card-network", "kind": "external"},
    })
    for f in faults:
        if f.fault_type == "bad_deployment":
            at = f.start - timedelta(seconds=int(rng.integers(60, 240)))
            records.append({
                "timestamp": at.isoformat(), "service": f.service, "type": "deployment",
                "message": f"Deployed {f.service}", "attributes": {"version": _version(rng), "commit": sim.hex(8), "deployed_by": "ci-pipeline"},
            })
    for service, at in decoy_deployments or []:
        records.append({
            "timestamp": at.isoformat(), "service": service, "type": "deployment",
            "message": f"Deployed {service}", "attributes": {"version": _version(rng), "commit": sim.hex(8), "deployed_by": "ci-pipeline"},
        })

    for minute in range(minutes):
        minute_start = start + timedelta(minutes=minute)
        n_requests = int(rng.poisson(requests_per_minute))
        offsets = np.sort(rng.uniform(0, 60, n_requests))
        for offset in offsets:
            t = minute_start + timedelta(seconds=float(offset))
            sim.span(_route_choice(rng), t, sim.hex(32), None)
        # per-minute resource metrics
        for service in SERVICES:
            if service == "web-frontend":
                continue
            pool = 0.35 + rng.normal(0, 0.03)
            cpu = 0.3 + rng.normal(0, 0.04)
            for f in faults:
                if f.service == service and f.active(minute_start) and f.fault_type == "resource_exhaustion":
                    progress = min(1.0, (minute_start - f.start).total_seconds() / 120.0 + 0.5)
                    pool = 0.35 + (0.97 - 0.35) * progress + rng.normal(0, 0.01)
                    cpu += 0.25 * progress
            metric_ts = (minute_start + timedelta(seconds=59)).isoformat()
            records.append({"timestamp": metric_ts, "service": service, "type": "metric",
                             "metric": "cpu_utilization", "value": round(float(np.clip(cpu, 0, 1)), 4)})
            if service in DATABASES or service.endswith("-service"):
                records.append({"timestamp": metric_ts, "service": service, "type": "metric",
                                "metric": "connection_pool_utilization", "value": round(float(np.clip(pool, 0, 1)), 4)})

    for idx, f in enumerate(faults):
        f.affected_services = sorted(
            s for (i, s), (hit, total) in sim.influence.items() if i == idx and total and hit / total >= 0.05
        )
        if f.service not in f.affected_services:
            f.affected_services.append(f.service)
    records.sort(key=lambda r: r["timestamp"])
    return Scenario(scenario_id, seed, start, minutes, faults, [
        {k: v for k, v in w.items() if not k.endswith("_dt")} for w in noise
    ], records, description)


def _callers_of(service: str) -> set[str]:
    callers: set[str] = set()

    def walk(node: CallNode, path: list[str]) -> None:
        if node.service == service:
            callers.update(path)
        for child in node.children:
            walk(child, path + [node.service])

    for _, _, root in ROUTES:
        walk(root, [])
    return callers


def _callees_of(service: str) -> set[str]:
    callees: set[str] = set()

    def collect(node: CallNode) -> None:
        for child in node.children:
            callees.add(child.service)
            collect(child)

    def find(node: CallNode) -> None:
        if node.service == service:
            collect(node)
        for child in node.children:
            find(child)

    for _, _, root in ROUTES:
        find(root)
    return callees


def random_scenario(seed: int, index: int = 0, start: datetime | None = None, minutes: int = 60,
                    multi_fault: bool | None = None) -> Scenario:
    """Build a random-but-deterministic benchmark scenario."""
    rng = np.random.default_rng(seed * 10_007 + index)
    start = start or datetime(2026, 1, 15, 9, 0, tzinfo=UTC) + timedelta(hours=index)
    multi = bool(rng.random() < 0.2) if multi_fault is None else multi_fault
    fault_minute = int(rng.integers(32, 44))
    faults: list[Fault] = []
    used: set[str] = set()
    for k in range(2 if multi else 1):
        # A second fault goes into a different branch: not the first fault's service, its
        # callers or its callees, so each fault has an unambiguous true cause.
        candidates = [s for s in FAULTABLE if s not in used and s != "api-gateway"]
        service = str(rng.choice(candidates))
        used |= {service} | _callers_of(service) | _callees_of(service)
        fault_type = str(rng.choice(FAULT_TYPES))
        f_start = start + timedelta(minutes=fault_minute + (int(rng.integers(0, 4)) if k else 0), seconds=int(rng.integers(0, 60)))
        duration = int(rng.integers(8, 15))
        templates = FAULT_MESSAGES[fault_type]
        faults.append(Fault(service, fault_type, f_start, f_start + timedelta(minutes=duration),
                            float(rng.uniform(0.3, 1.0)), str(templates[int(rng.integers(0, len(templates)))])))

    noise: list[dict] = []
    quiet = [s for s in FAULTABLE if s not in used] or [s for s in FAULTABLE if s not in {f.service for f in faults}]
    if rng.random() < 0.35:
        n_start = start + timedelta(minutes=int(rng.integers(14, 22)))
        noise.append({"service": str(rng.choice(quiet)), "start": n_start.isoformat(),
                      "end": (n_start + timedelta(minutes=2)).isoformat(), "message": "transient failure: cache node evicted"})
    decoys: list[tuple[str, datetime]] = []
    for _ in range(int(rng.integers(1, 3))):
        decoys.append((str(rng.choice([s for s in FAULTABLE if s not in {f.service for f in faults}])),
                       start + timedelta(minutes=int(rng.integers(5, 55)))))
    if rng.random() < 0.4:
        near = faults[0].start - timedelta(minutes=int(rng.integers(1, 8)))
        decoys.append((str(rng.choice(quiet)), near))
    missing = float(rng.choice([0.0, 0.0, 0.1, 0.3]))
    desc = ", ".join(f"{f.fault_type} in {f.service}" for f in faults)
    return simulate(
        seed=seed * 7919 + index, faults=faults, start=start, minutes=minutes,
        requests_per_minute=float(rng.uniform(22, 36)), missing_trace_fraction=missing,
        noise_windows=noise, decoy_deployments=decoys, scenario_id=f"s{seed}-{index:03d}",
        description=desc + (f"; {missing:.0%} logs without trace IDs" if missing else ""),
    )


def demo_scenario(end: datetime | None = None) -> Scenario:
    """The fixed, clearly-labelled synthetic dataset behind the public demo project:
    two faults over two hours plus a decoy deployment and a noise blip."""
    end = (end or datetime.now(UTC)).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=120)
    faults = [
        Fault("payments-service", "bad_deployment", start + timedelta(minutes=48), start + timedelta(minutes=60), 0.8,
              FAULT_MESSAGES["bad_deployment"][0]),
        Fault("inventory-db", "resource_exhaustion", start + timedelta(minutes=92), start + timedelta(minutes=104), 0.9,
              FAULT_MESSAGES["resource_exhaustion"][0]),
    ]
    noise = [{"service": "search-service", "start": (start + timedelta(minutes=22)).isoformat(),
              "end": (start + timedelta(minutes=24)).isoformat(), "message": "transient failure: cache node evicted"}]
    decoys = [("catalog-service", start + timedelta(minutes=44)), ("auth-service", start + timedelta(minutes=12))]
    return simulate(seed=20260115, faults=faults, start=start, minutes=120, requests_per_minute=24,
                    missing_trace_fraction=0.1, noise_windows=noise, decoy_deployments=decoys,
                    scenario_id="demo", description="ShopFlow synthetic demo: payments bad deploy + inventory-db pool exhaustion")


def summarize(scenario: Scenario) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for r in scenario.records:
        counts[r["type"]] += 1
    return {"records": len(scenario.records), "by_type": dict(counts), "minutes": scenario.minutes,
            "faults": len(scenario.faults)}
