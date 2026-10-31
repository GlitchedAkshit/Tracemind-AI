import uuid

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import IngestionJob, User
from app.schemas import IngestionOut, Page
from app.security import current_user, require_project
from app.services import ingestion as svc

router = APIRouter(tags=["ingestion"])


async def _read_limited(upload: UploadFile) -> bytes:
    limit = get_settings().max_upload_bytes
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "payload_too_large", f"Files are limited to {limit // (1024 * 1024)} MB.")
    return data


def _env(environment: str, allowed: list[str]) -> str:
    env = environment.strip().lower()
    if env not in allowed:
        raise ApiError(422, "invalid_environment", f"Environment must be one of: {', '.join(allowed)}.")
    return env


@router.post("/projects/{project_id}/ingestions/preview")
async def preview_ingestion(project_id: uuid.UUID, file: UploadFile = File(...), environment: str = Form("production"),
                            service_mapping: str | None = Form(None), user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
    project = require_project(db, user, project_id, "editor")
    content = await _read_limited(file)
    return svc.preview(content, file.filename or "", svc.parse_mapping(service_mapping), _env(environment, project.environments))


@router.post("/projects/{project_id}/ingestions", response_model=IngestionOut, status_code=202)
async def create_ingestion(project_id: uuid.UUID, response: Response, file: UploadFile = File(...),
                           environment: str = Form("production"), service_mapping: str | None = Form(None),
                           idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=128),
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id, "editor")
    content = await _read_limited(file)
    job, created = svc.create_job(db, project, user, file.filename or "", content, _env(environment, project.environments),
                                  svc.parse_mapping(service_mapping), idempotency_key)
    if not created:
        response.status_code = 200
    return job


@router.get("/projects/{project_id}/ingestions", response_model=Page[IngestionOut])
def list_ingestions(project_id: uuid.UUID, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    base = select(IngestionJob).where(IngestionJob.project_id == project_id)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    items = db.scalars(base.order_by(IngestionJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[IngestionOut.model_validate(i) for i in items], total=total, page=page, page_size=page_size)


@router.get("/ingestions/{job_id}", response_model=IngestionOut)
def get_ingestion(job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise ApiError(404, "not_found", "Ingestion job not found.")
    try:
        require_project(db, user, job.project_id)
    except ApiError as exc:
        raise ApiError(404, "not_found", "Ingestion job not found.") from exc
    return job
