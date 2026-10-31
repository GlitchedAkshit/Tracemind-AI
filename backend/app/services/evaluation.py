"""Benchmark runs triggered from the API; results are persisted for the Evaluation Lab."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import insert

from app.analysis.ranking import STRATEGIES
from app.analysis.types import ALGORITHM_VERSION, AnalysisConfig
from app.bench.runner import run_benchmark
from app.db import SessionLocal
from app.errors import ApiError
from app.models import EvaluationResult, EvaluationRun, User
from app.services import jobs

log = logging.getLogger("tracemind.evaluation")


def create_evaluation(db, user: User, seed: int, scenario_count: int, strategies: list[str] | None) -> EvaluationRun:
    strategies = strategies or list(STRATEGIES)
    unknown = [s for s in strategies if s not in STRATEGIES]
    if unknown:
        raise ApiError(422, "invalid_strategy", f"Unknown strategies: {', '.join(unknown)}")
    run = EvaluationRun(id=uuid.uuid4(), created_by=user.id, algorithm_version=ALGORITHM_VERSION, status="queued",
                        seed=seed, scenario_count=scenario_count,
                        config={"strategies": strategies, "analysis": AnalysisConfig().to_dict(), "summary_writer": "deterministic"})
    db.add(run)
    db.commit()
    jobs.submit(execute_evaluation, run.id)
    return run


def execute_evaluation(run_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        run = db.get(EvaluationRun, run_id)
        if run is None or run.status != "queued":
            return
        run.status, run.started_at = "running", datetime.now(UTC)
        db.commit()

        def progress(done: int, total: int) -> None:
            run.progress = int(done * 100 / total)
            db.commit()

        rows, metrics = run_benchmark(run.seed, run.scenario_count, run.config["strategies"], progress=progress)
        db.execute(insert(EvaluationResult), [
            {"id": uuid.uuid4(), "evaluation_run_id": run.id, "scenario_id": r.scenario_id, "strategy": r.strategy,
             "fault_type": r.fault_type, "true_cause_service": r.true_cause_service, "predicted_services": r.predicted_services,
             "top1_correct": r.top1_correct, "top3_correct": r.top3_correct, "anomaly_tp": r.anomaly_tp,
             "anomaly_fp": r.anomaly_fp, "anomaly_fn": r.anomaly_fn, "anomaly_truth_total": r.anomaly_truth_total,
             "detected": r.detected, "multi_fault": r.multi_fault, "citations_total": r.citations_total,
             "citations_invalid": r.citations_invalid, "latency_ms": r.latency_ms}
            for r in rows
        ])
        run.metrics = metrics
        run.status, run.progress, run.completed_at = "completed", 100, datetime.now(UTC)
        db.commit()
    except Exception as exc:
        db.rollback()
        log.exception("evaluation failed")
        run = db.get(EvaluationRun, run_id)
        if run is not None:
            run.status, run.completed_at = "failed", datetime.now(UTC)
            run.error_message = f"{type(exc).__name__}: {str(exc)[:500]}"
            db.commit()
    finally:
        db.close()
