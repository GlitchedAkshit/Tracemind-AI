import re
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ApiError
from app.models import Project, ProjectMember, User
from app.schemas import MemberIn, MemberOut, ProjectIn, ProjectOut, ProjectPatch
from app.security import ROLE_RANK, current_user, project_role, require_project
from app.services.overview import RANGES, overview

router = APIRouter(tags=["projects"])
DEMO_USER_PROJECT_LIMIT = 3


def _out(project: Project, role: str | None) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.role = role
    return out


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.is_demo:
        owned = db.scalar(select(func.count(ProjectMember.id)).where(ProjectMember.user_id == user.id, ProjectMember.role == "owner"))
        if owned >= DEMO_USER_PROJECT_LIMIT:
            raise ApiError(403, "demo_limit", f"Demo sessions can create at most {DEMO_USER_PROJECT_LIMIT} sandbox projects.")
    base = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:100] or "project"
    project = Project(id=uuid.uuid4(), name=body.name.strip(), slug=f"{base}-{uuid.uuid4().hex[:6]}",
                      description=body.description, environments=body.environments, analysis_settings={},
                      created_by=user.id)
    project.members.append(ProjectMember(user_id=user.id, role="owner"))
    db.add(project)
    db.commit()
    return _out(project, "owner")


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Project, ProjectMember.role).join(ProjectMember, ProjectMember.project_id == Project.id)
                      .where(ProjectMember.user_id == user.id).order_by(Project.is_demo.desc(), Project.created_at))
    return [_out(p, role) for p, role in rows]


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return _out(project, project_role(db, user, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id, "owner")
    if body.name is not None:
        project.name = body.name.strip()
    if body.description is not None:
        project.description = body.description
    if body.environments is not None:
        project.environments = body.environments
    if body.analysis_settings is not None:
        project.analysis_settings = {**(project.analysis_settings or {}), **body.analysis_settings}
    db.commit()
    return _out(project, "owner")


@router.get("/projects/{project_id}/overview")
def project_overview(project_id: uuid.UUID, environment: str | None = None,
                     range: str = Query("7d", pattern="^(1h|6h|24h|7d|30d|all)$"),
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return overview(db, project, environment, range if range in RANGES or range == "all" else "7d")


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def list_members(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id)
    rows = db.execute(select(ProjectMember, User).join(User, User.id == ProjectMember.user_id)
                      .where(ProjectMember.project_id == project_id).order_by(ProjectMember.created_at))
    return [MemberOut(id=m.id, user_id=u.id, email="demo visitor" if u.is_demo else u.email, display_name=u.display_name,
                      role=m.role, created_at=m.created_at) for m, u in rows]


@router.post("/projects/{project_id}/members", response_model=MemberOut, status_code=201)
def add_member(project_id: uuid.UUID, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id, "owner")
    if project.is_demo:
        raise ApiError(403, "forbidden", "Membership of the demo project cannot be changed.")
    target = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if target is None or target.is_demo:
        raise ApiError(404, "not_found", "No user with that email.")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == target.id))
    if member is None:
        member = ProjectMember(project_id=project_id, user_id=target.id, role=body.role)
        db.add(member)
    else:
        member.role = body.role
    db.commit()
    return MemberOut(id=member.id, user_id=target.id, email=target.email, display_name=target.display_name,
                     role=member.role, created_at=member.created_at)


@router.delete("/projects/{project_id}/members/{member_id}", status_code=204)
def remove_member(project_id: uuid.UUID, member_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_project(db, user, project_id, "owner")
    member = db.get(ProjectMember, member_id)
    if member is None or member.project_id != project_id:
        raise ApiError(404, "not_found", "Member not found.")
    owners = db.scalar(select(func.count(ProjectMember.id)).where(ProjectMember.project_id == project_id, ProjectMember.role == "owner"))
    if member.role == "owner" and owners <= 1:
        raise ApiError(409, "last_owner", "A project must keep at least one owner.")
    db.delete(member)
    db.commit()


__all__ = ["router", "ROLE_RANK"]
