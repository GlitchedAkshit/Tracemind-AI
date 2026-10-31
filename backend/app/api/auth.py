from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import User
from app.ratelimit import limiter
from app.schemas import LoginIn, RegisterIn, SessionOut, UserOut
from app.security import COOKIE_NAME, create_token, current_user, hash_password, verify_password
from app.seed import DEMO_EMAIL, ensure_demo_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _session(response: Response, user: User) -> SessionOut:
    settings = get_settings()
    token = create_token(user)
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.cookie_secure, samesite="lax",
                        max_age=settings.access_token_ttl_minutes * 60, path="/")
    return SessionOut(user=UserOut.model_validate(user), token=token)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=SessionOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    limiter.check(f"register:{_client(request)}", 10, 3600)
    email = body.email.lower()
    if email == DEMO_EMAIL or db.scalar(select(User).where(func.lower(User.email) == email)):
        raise ApiError(409, "email_taken", "An account with this email already exists.")
    user = User(email=email, display_name=body.display_name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return _session(response, user)


@router.post("/login", response_model=SessionOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    limiter.check(f"login:{_client(request)}", 20, 300)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or user.is_demo or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")
    return _session(response, user)


@router.post("/demo", response_model=SessionOut)
def demo_login(request: Request, response: Response, db: Session = Depends(get_db)):
    if not get_settings().demo_mode:
        raise ApiError(404, "not_found", "Demo mode is disabled.")
    limiter.check(f"demo:{_client(request)}", 30, 300)
    return _session(response, ensure_demo_user(db))


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
