import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.ranking import STRATEGIES
from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import EvaluationResult, EvaluationRun, User
from app.ratelimit import limiter
from app.schemas import EvaluationIn, EvaluationResultOut, EvaluationRunOut
from app.security import current_user
from app.services.evaluation import create_evaluation

router = APIRouter(tags=["evaluation"])


@router.get("/evaluation/strategies")
def strategies(user: User = Depends(current_user)):
    return [{"name": s.name, "description": s.description, "weights": s.weights,
             "contradiction_weight": s.contradiction_weight, "rules_only": s.rules_only} for s in STRATEGIES.values()]


@router.post("/evaluation-runs", response_model=EvaluationRunOut, status_code=202)
def start_evaluation(body: EvaluationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    # Benchmarks only use generated synthetic scenarios, so they are not project-scoped.
    limiter.check(f"evaluation:{user.id}", max(1, get_settings().analysis_rate_limit_per_minute // 3), 300)
    return create_evaluation(db, user, body.seed, body.scenario_count, body.strategies)


@router.get("/evaluation-runs", response_model=list[EvaluationRunOut])
def list_evaluations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(50)).all()


@router.get("/evaluation-runs/{run_id}", response_model=EvaluationRunOut)
def get_evaluation(run_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Evaluation run not found.")
    return run


@router.get("/evaluation-runs/{run_id}/results", response_model=list[EvaluationResultOut])
def get_results(run_id: uuid.UUID, strategy: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_evaluation(run_id, user, db)
    query = select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run_id)
    if strategy:
        query = query.where(EvaluationResult.strategy == strategy)
    return db.scalars(query.order_by(EvaluationResult.scenario_id, EvaluationResult.strategy)).all()


@router.get("/evaluation-runs/{run_id}/export")
def export_results(run_id: uuid.UUID, format: str = Query("json", pattern="^(json|csv)$"),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = get_evaluation(run_id, user, db)
    results = [EvaluationResultOut.model_validate(r).model_dump() for r in get_results(run_id, None, user, db)]
    name = f"tracemind-eval-{run.algorithm_version}-seed{run.seed}-n{run.scenario_count}"
    if format == "csv":
        buf = io.StringIO()
        if results:
            writer = csv.DictWriter(buf, fieldnames=list(results[0]))
            writer.writeheader()
            for r in results:
                writer.writerow({**r, "predicted_services": "|".join(r["predicted_services"])})
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    body = {"run": EvaluationRunOut.model_validate(run).model_dump(mode="json"), "results": results}
    return PlainTextResponse(json.dumps(body, indent=2, default=str), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
