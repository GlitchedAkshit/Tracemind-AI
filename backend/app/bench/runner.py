"""Reproducible benchmark: synthetic fault scenarios scored against known injected causes.

For every scenario the detector runs once; every ranking strategy (baseline, ablations and
the full hybrid) then ranks the incident that overlaps each injected fault. Metrics:

* top-1 / top-3 root-cause accuracy per injected fault (a missed detection counts as wrong)
* anomaly precision / recall at (service, time-window) level against ground truth that
  includes propagated impact and injected noise blips
* invalid-citation rate of the summary writer (evidence IDs not present in the input)
* analysis latency (detection + ranking + summary), wall-clock on this machine

Usage::

    python -m app.bench.runner --seed 42 --scenarios 40 --out ../benchmarks/results
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.analysis.pipeline import analyze_incident, run_detection
from app.analysis.ranking import STRATEGIES
from app.analysis.types import ALGORITHM_VERSION, AnalysisConfig, Anomaly, CandidateIncident
from app.bench.generator import Fault, Scenario, random_scenario
from app.llm.evidence import build_bundle, event_to_dict, hypothesis_to_dict
from app.llm.providers import LLMProvider
from app.llm.writer import write_summary

MATCH_MARGIN = timedelta(minutes=2)


@dataclass
class ResultRow:
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
    anomaly_truth_total: int
    citations_total: int
    citations_invalid: int
    latency_ms: float
    detected: bool
    multi_fault: bool = False


def _overlaps(a_start, a_end, b_start, b_end, margin=MATCH_MARGIN) -> bool:
    return a_start <= b_end + margin and b_start <= a_end + margin


def match_incident(fault: Fault, incidents: list[CandidateIncident]) -> CandidateIncident | None:
    affected = set(fault.affected_services)
    best, best_overlap = None, 0
    for inc in incidents:
        if not _overlaps(inc.started_at, inc.ended_at, fault.start, fault.end):
            continue
        overlap = len(affected & set(inc.services))
        if overlap > best_overlap:
            best, best_overlap = inc, overlap
    return best


def anomaly_scores(scenario: Scenario, anomalies: list[Anomaly]) -> tuple[int, int, int, int]:
    """Return (tp detections, fp detections, fn truth windows, truth windows)."""
    truth = [(s, f.start, f.end) for f in scenario.faults for s in f.affected_services]
    truth += [(w["service"], datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"])) for w in scenario.noise_windows]
    matched_truth: set[int] = set()
    tp = fp = 0
    for a in anomalies:
        hits = [i for i, (s, start, end) in enumerate(truth) if s == a.service and _overlaps(a.window_start, a.window_end, start, end)]
        if hits:
            tp += 1
            matched_truth.update(hits)
        else:
            fp += 1
    return tp, fp, len(truth) - len(matched_truth), len(truth)


def evaluate_scenario(scenario: Scenario, strategies: list[str], cfg: AnalysisConfig,
                      provider: LLMProvider | None = None) -> list[ResultRow]:
    events = scenario.to_events()
    t0 = time.perf_counter()
    detection = run_detection(events, cfg)
    detect_ms = (time.perf_counter() - t0) * 1000
    tp, fp, fn, truth_total = anomaly_scores(scenario, detection.anomalies)
    event_dicts = {e.id: event_to_dict(e) for e in events}
    rows: list[ResultRow] = []
    for fault in scenario.faults:
        incident = match_incident(fault, detection.incidents)
        for strategy in strategies:
            t1 = time.perf_counter()
            predicted: list[str] = []
            citations_total = citations_invalid = 0
            if incident is not None:
                hypotheses = analyze_incident(incident, events, detection.graph, cfg, strategy)
                predicted = [h.service for h in hypotheses]
                if strategy == "hybrid":
                    hyp_dicts = [hypothesis_to_dict(h) for h in hypotheses]
                    referenced = {ev["event_id"] for h in hyp_dicts for ev in h["evidence"]}
                    bundle = build_bundle(
                        {"title": incident.title, "severity": incident.severity, "started_at": incident.started_at,
                         "ended_at": incident.ended_at, "affected_services": incident.services},
                        hyp_dicts, [event_dicts[i] for i in referenced if i in event_dicts],
                    )
                    summary, meta = write_summary(bundle, provider)
                    cited = [eid for h in summary["hypotheses"] for eid in h["supporting_evidence_ids"] + h["contradicting_evidence_ids"]]
                    cited += [ev["event_id"] for h in hyp_dicts for ev in h["evidence"]]
                    citations_total = len(cited) + meta.get("citations_rejected", 0)
                    citations_invalid = sum(1 for c in cited if c not in event_dicts) + meta.get("citations_rejected", 0)
            latency = detect_ms + (time.perf_counter() - t1) * 1000
            rows.append(ResultRow(
                scenario_id=scenario.scenario_id, strategy=strategy, fault_type=fault.fault_type,
                true_cause_service=fault.service, predicted_services=predicted[:5],
                top1_correct=bool(predicted) and predicted[0] == fault.service,
                top3_correct=fault.service in predicted[:3],
                anomaly_tp=tp, anomaly_fp=fp, anomaly_fn=fn, anomaly_truth_total=truth_total,
                citations_total=citations_total, citations_invalid=citations_invalid,
                latency_ms=round(latency, 2), detected=incident is not None,
                multi_fault=len(scenario.faults) > 1,
            ))
    return rows


def aggregate(rows: list[ResultRow]) -> dict:
    by_strategy: dict[str, list[ResultRow]] = defaultdict(list)
    for r in rows:
        by_strategy[r.strategy].append(r)
    # anomaly metrics are strategy-independent: count each scenario once
    per_scenario = {r.scenario_id: r for r in rows}
    tp = sum(r.anomaly_tp for r in per_scenario.values())
    fp = sum(r.anomaly_fp for r in per_scenario.values())
    fn = sum(r.anomaly_fn for r in per_scenario.values())
    truth = sum(r.anomaly_truth_total for r in per_scenario.values())
    strategies = {}
    for name, items in by_strategy.items():
        latencies = sorted(r.latency_ms for r in items)
        by_fault: dict[str, list[ResultRow]] = defaultdict(list)
        for r in items:
            by_fault[r.fault_type].append(r)
        cit_total = sum(r.citations_total for r in items)
        strategies[name] = {
            "description": STRATEGIES[name].description,
            "faults": len(items),
            "top1_accuracy": round(sum(r.top1_correct for r in items) / len(items), 4),
            "top3_accuracy": round(sum(r.top3_correct for r in items) / len(items), 4),
            "detection_rate": round(sum(r.detected for r in items) / len(items), 4),
            "latency_ms_mean": round(statistics.fmean(latencies), 2),
            "latency_ms_p95": latencies[min(len(latencies) - 1, int(0.95 * (len(latencies) - 1) + 0.5))],
            "citations_total": cit_total,
            "invalid_citation_rate": round(sum(r.citations_invalid for r in items) / cit_total, 4) if cit_total else None,
            "by_scenario_kind": {
                kind: {"n": len(rs), "top1": round(sum(r.top1_correct for r in rs) / len(rs), 4),
                       "top3": round(sum(r.top3_correct for r in rs) / len(rs), 4)}
                for kind, rs in (("single_fault", [r for r in items if not r.multi_fault]),
                                 ("multi_fault", [r for r in items if r.multi_fault])) if rs
            },
            "by_fault_type": {
                ft: {"n": len(rs), "top1": round(sum(r.top1_correct for r in rs) / len(rs), 4),
                     "top3": round(sum(r.top3_correct for r in rs) / len(rs), 4)}
                for ft, rs in sorted(by_fault.items())
            },
        }
    return {
        "scenarios": len(per_scenario),
        "injected_faults": len(by_strategy.get("hybrid", next(iter(by_strategy.values()), []))),
        "anomaly_precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "anomaly_recall": round((truth - fn) / truth, 4) if truth else None,
        "anomaly_detections": tp + fp,
        "anomaly_truth_windows": truth,
        "strategies": strategies,
    }


def run_benchmark(seed: int, scenario_count: int, strategies: list[str] | None = None,
                  cfg: AnalysisConfig | None = None, provider: LLMProvider | None = None,
                  progress: Callable[[int, int], None] | None = None) -> tuple[list[ResultRow], dict]:
    cfg = cfg or AnalysisConfig()
    strategies = strategies or list(STRATEGIES)
    if "hybrid" not in strategies:
        strategies = [*strategies, "hybrid"]
    rows: list[ResultRow] = []
    started = time.perf_counter()
    for index in range(scenario_count):
        scenario = random_scenario(seed, index)
        rows.extend(evaluate_scenario(scenario, strategies, cfg, provider))
        if progress:
            progress(index + 1, scenario_count)
    metrics = aggregate(rows)
    metrics.update(
        algorithm_version=ALGORITHM_VERSION,
        seed=seed,
        config=cfg.to_dict(),
        summary_writer=provider.model_version if provider else "deterministic-writer/1.0",
        wall_clock_seconds=round(time.perf_counter() - started, 2),
        environment={"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        generated_at=datetime.now(UTC).isoformat(),
    )
    return rows, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="TraceMind synthetic root-cause benchmark")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scenarios", type=int, default=40)
    parser.add_argument("--strategies", default=",".join(STRATEGIES))
    parser.add_argument("--out", default="benchmark-results")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, metrics = run_benchmark(
        args.seed, args.scenarios, args.strategies.split(","),
        progress=lambda i, n: print(f"\rscenario {i}/{n}", end="", flush=True),
    )
    print()
    stem = f"benchmark-seed{args.seed}-n{args.scenarios}"
    (out / f"{stem}.json").write_text(json.dumps({"metrics": metrics, "results": [asdict(r) for r in rows]}, indent=2))
    with (out / f"{stem}.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        for r in rows:
            writer.writerow({**asdict(r), "predicted_services": "|".join(r.predicted_services)})
    print(json.dumps({k: v for k, v in metrics.items() if k != "config"}, indent=2))
    print(f"wrote {out / stem}.json and .csv")


if __name__ == "__main__":
    main()
