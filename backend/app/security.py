"""Authentication (password hashing, JWT session tokens) and project-scoped authorization."""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import Incident, Project, ProjectMember, User

COOKIE_NAME = "tm_session"
PBKDF2_ITERATIONS = 310_000
ROLE_RANK = {"viewer": 0, "analyst": 1, "editor": 2, "owner": 3}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def create_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
        "demo": user.is_demo,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    if not token:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise ApiError(401, "invalid_token", "Session is invalid or expired.") from exc
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(401, "invalid_token", "Session is invalid or expired.")
    request.state.user_id = str(user.id)
    return user


def project_role(db: Session, user: User, project_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    )


def require_project(db: Session, user: User, project_id: uuid.UUID, min_role: str = "viewer") -> Project:
    """Return the project if ``user`` holds at least ``min_role`` on it.

    Non-members get 404 (not 403) so project existence is not leaked across tenants."""
    project = db.get(Project, project_id)
    role = project_role(db, user, project_id) if project else None
    if project is None or role is None:
        raise ApiError(404, "not_found", "Project not found.")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise ApiError(403, "forbidden", f"This action requires the {min_role} role on the project.")
    return project


def require_incident(db: Session, user: User, incident_id: uuid.UUID, min_role: str = "viewer") -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise ApiError(404, "not_found", "Incident not found.")
    try:
        require_project(db, user, incident.project_id, min_role)
    except ApiError as exc:
        if exc.status_code == 404:
            raise ApiError(404, "not_found", "Incident not found.") from exc
        raise
    return incident
