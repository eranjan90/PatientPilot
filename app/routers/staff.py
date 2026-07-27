"""Staff-facing routes. Every route here is behind `require_staff`, enforced in backend code."""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_staff
from app.database import get_db
from app.models import (
    AppointmentSlot,
    Department,
    Doctor,
    Escalation,
    EscalationStatus,
    PatientProfile,
    SlotStatus,
    User,
    WorkflowRun,
)
from app.tools import escalation_tools
from app.tools.audit_tools import list_recent_events

router = APIRouter(prefix="/staff", tags=["staff"])
templates = Jinja2Templates(directory="app/templates")


@router.get("")
def dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    runs = db.query(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(50).all()
    pending_escalations = escalation_tools.list_pending_escalations(db)
    return templates.TemplateResponse(
        "staff/dashboard.html",
        {"request": request, "user": user, "runs": runs, "pending_escalations": pending_escalations},
    )


@router.get("/workflow/{run_id}")
def workflow_detail(request: Request, run_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    run = db.get(WorkflowRun, run_id)
    state = json.loads(run.state or "{}") if run else {}
    escalation = db.query(Escalation).filter_by(workflow_run_id=run_id).order_by(Escalation.created_at.desc()).first()
    patient = db.get(PatientProfile, run.patient_id) if run else None
    return templates.TemplateResponse(
        "staff/workflow_detail.html",
        {"request": request, "user": user, "run": run, "state": state, "escalation": escalation, "patient": patient},
    )


@router.get("/escalations")
def escalations(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    pending = escalation_tools.list_pending_escalations(db)
    resolved = (
        db.query(Escalation)
        .filter(Escalation.status != EscalationStatus.pending)
        .order_by(Escalation.created_at.desc())
        .limit(30)
        .all()
    )
    return templates.TemplateResponse(
        "staff/escalations.html", {"request": request, "user": user, "pending": pending, "resolved": resolved}
    )


@router.post("/escalations/{escalation_id}/approve")
def approve_escalation(
    escalation_id: int, notes: str = Form(""), user: User = Depends(require_staff), db: Session = Depends(get_db)
):
    escalation_tools.review_escalation(db, escalation_id, approve=True, reviewer_id=user.id, notes=notes)
    return RedirectResponse("/staff/escalations", status_code=303)


@router.post("/escalations/{escalation_id}/reject")
def reject_escalation(
    escalation_id: int, notes: str = Form(""), user: User = Depends(require_staff), db: Session = Depends(get_db)
):
    escalation_tools.review_escalation(db, escalation_id, approve=False, reviewer_id=user.id, notes=notes)
    return RedirectResponse("/staff/escalations", status_code=303)


@router.get("/departments")
def departments(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    depts = db.query(Department).all()
    doctors = db.query(Doctor).all()
    return templates.TemplateResponse(
        "staff/departments.html", {"request": request, "user": user, "departments": depts, "doctors": doctors}
    )


@router.post("/departments/add")
def add_department(
    name: str = Form(...),
    description: str = Form(""),
    required_documents: str = Form(""),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from app.tools.audit_tools import log_event

    dept = Department(name=name, description=description, required_documents=required_documents, active=True)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    log_event(db, "staff_admin", "department_added", "Department", dept.id, actor_id=user.id)
    return RedirectResponse("/staff/departments", status_code=303)


@router.post("/departments/{department_id}/add_doctor")
def add_doctor(
    department_id: int,
    name: str = Form(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    from app.tools.audit_tools import log_event

    doctor = Doctor(department_id=department_id, name=name, active=True)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    log_event(db, "staff_admin", "doctor_added", "Doctor", doctor.id, actor_id=user.id, metadata={"department_id": department_id})
    return RedirectResponse("/staff/departments", status_code=303)


@router.post("/doctors/{doctor_id}/add_slot")
def add_slot(
    doctor_id: int,
    start_time: str = Form(...),
    duration_minutes: int = Form(30),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    start = datetime.fromisoformat(start_time)
    slot = AppointmentSlot(
        doctor_id=doctor_id,
        start_time=start,
        end_time=start + timedelta(minutes=duration_minutes),
        status=SlotStatus.available,
    )
    db.add(slot)
    db.commit()
    from app.tools.audit_tools import log_event

    log_event(db, "staff_admin", "slot_added", "AppointmentSlot", slot.id, actor_id=user.id)
    return RedirectResponse("/staff/departments", status_code=303)


@router.get("/audit")
def audit_log(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    events = list_recent_events(db, limit=300)
    return templates.TemplateResponse("staff/audit.html", {"request": request, "user": user, "events": events})


@router.get("/patients/{patient_id}")
def patient_detail(
    request: Request, patient_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)
):
    patient = db.get(PatientProfile, patient_id)
    runs = db.query(WorkflowRun).filter_by(patient_id=patient_id).order_by(WorkflowRun.created_at.desc()).all()
    return templates.TemplateResponse(
        "staff/patient_detail.html", {"request": request, "user": user, "patient": patient, "runs": runs}
    )
