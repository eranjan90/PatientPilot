"""Authentication + backend-enforced RBAC.

Sessions are signed cookies (itsdangerous) holding the user id. Role checks happen here,
in dependency functions used by every protected route — NOT just hidden in the frontend.
"""
from __future__ import annotations

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User, UserRole

serializer = URLSafeTimedSerializer(settings.SESSION_SECRET, salt="agentcare-session")

SESSION_COOKIE = "agentcare_session"


def hash_password(password: str) -> str:
    # bcrypt has a 72-byte input limit; truncate defensively rather than raising.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session_token(user_id: int) -> str:
    return serializer.dumps({"user_id": user_id})


def read_session_token(token: str) -> int | None:
    try:
        data = serializer.loads(token, max_age=60 * 60 * 24 * 7)  # 7 days
        return data.get("user_id")
    except BadSignature:
        return None


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    if not session:
        return None
    user_id = read_session_token(session)
    if not user_id:
        return None
    return db.get(User, user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_staff(user: User = Depends(require_user)) -> User:
    """Enforced in backend code, not just hidden UI: only staff/admin may pass."""
    if user.role not in (UserRole.staff, UserRole.admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return user


def require_patient(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.patient:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Patient access required")
    return user


def assert_owns_patient(user: User, patient_id: int) -> None:
    """A patient may only ever act on their own PatientProfile. Staff bypass this check."""
    if user.role in (UserRole.staff, UserRole.admin):
        return
    if not user.patient_profile or user.patient_profile.id != patient_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your record")
