"""Login / registration routes. Only patients can self-register — staff accounts are
provisioned via the seed script / by an admin, which keeps role assignment out of user
control (role is never a field a client can set on themselves)."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, create_session_token, get_current_user, hash_password, verify_password
from app.database import get_db
from app.models import PatientProfile, User, UserRole
from app.tools.audit_tools import log_event

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login")
def login_form(request: Request, user=Depends(get_current_user)):
    if user:
        dest = "/staff" if user.role in (UserRole.staff, UserRole.admin) else "/patient"
        return RedirectResponse(dest, status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter_by(email=email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid email or password"}, status_code=401
        )
    token = create_session_token(user.id)
    log_event(db, "auth", "login", "User", user.id, actor_id=user.id)
    dest = "/staff" if user.role in (UserRole.staff, UserRole.admin) else "/patient"
    response = RedirectResponse(dest, status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@router.get("/register")
def register_form(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse("/patient", status_code=303)
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/register")
def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    date_of_birth: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    if db.query(User).filter_by(email=email_norm).first():
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "An account with this email already exists"}, status_code=400
        )

    # Role is always hardcoded to `patient` here — never taken from client input.
    user = User(name=name, email=email_norm, password_hash=hash_password(password), role=UserRole.patient)
    db.add(user)
    db.flush()
    profile = PatientProfile(user_id=user.id, date_of_birth=date_of_birth or None, phone=phone or None)
    db.add(profile)
    db.commit()
    log_event(db, "auth", "patient_registered", "User", user.id, actor_id=user.id)

    token = create_session_token(user.id)
    response = RedirectResponse("/patient", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
