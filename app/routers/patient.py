"""Patient-facing routes. Every route here is behind `require_patient` (backend-enforced,
not just hidden UI) and every data access double-checks the record belongs to the logged-in
patient via `assert_owns_patient`."""
import json

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.agents.coordinator import CoordinatorAgent
from app.auth import assert_owns_patient, require_patient
from app.database import get_db
from app.models import Appointment, Escalation, PatientDocument, Reminder, User, WorkflowRun
from app.tools import appointment_tools, department_tools
from app.tools.audit_tools import log_event

router = APIRouter(prefix="/patient", tags=["patient"])
templates = Jinja2Templates(directory="app/templates")
coordinator = CoordinatorAgent()


@router.get("")
def dashboard(request: Request, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    profile = user.patient_profile
    runs = (
        db.query(WorkflowRun).filter_by(patient_id=profile.id).order_by(WorkflowRun.created_at.desc()).all()
    )
    appointments = (
        db.query(Appointment).filter_by(patient_id=profile.id).order_by(Appointment.created_at.desc()).all()
    )
    reminders = db.query(Reminder).filter_by(patient_id=profile.id).order_by(Reminder.scheduled_at.asc()).all()
    documents = db.query(PatientDocument).filter_by(patient_id=profile.id).all()
    return templates.TemplateResponse(
        "patient/dashboard.html",
        {
            "request": request, "user": user, "runs": runs, "appointments": appointments,
            "reminders": reminders, "documents": documents,
        },
    )


@router.get("/request")
def new_request_form(request: Request, user: User = Depends(require_patient)):
    return templates.TemplateResponse("patient/new_request.html", {"request": request, "user": user})


@router.post("/request")
async def submit_request(
    request: Request,
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    profile = user.patient_profile
    uploaded_files = []
    for f in files:
        if f and f.filename:
            content = await f.read()
            if content:
                uploaded_files.append((f.filename, content))

    result = coordinator.run(db, profile.id, message, uploaded_files or None)
    return RedirectResponse(f"/patient/workflow/{result['workflow_run_id']}", status_code=303)


@router.get("/workflow/{run_id}")
def workflow_status(
    request: Request, run_id: int, user: User = Depends(require_patient), db: Session = Depends(get_db)
):
    run = db.get(WorkflowRun, run_id)
    if not run:
        return RedirectResponse("/patient", status_code=303)
    assert_owns_patient(user, run.patient_id)
    state = json.loads(run.state or "{}")
    escalation = db.query(Escalation).filter_by(workflow_run_id=run.id).order_by(Escalation.created_at.desc()).first()
    return templates.TemplateResponse(
        "patient/workflow_status.html",
        {"request": request, "user": user, "run": run, "state": state, "escalation": escalation},
    )


@router.get("/appointments")
def appointments_list(request: Request, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    profile = user.patient_profile
    appointments = db.query(Appointment).filter_by(patient_id=profile.id).order_by(Appointment.created_at.desc()).all()
    return templates.TemplateResponse(
        "patient/appointments.html", {"request": request, "user": user, "appointments": appointments}
    )


@router.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: int, user: User = Depends(require_patient), db: Session = Depends(get_db)
):
    appt = db.get(Appointment, appointment_id)
    if appt:
        assert_owns_patient(user, appt.patient_id)
        appointment_tools.cancel_appointment(db, appointment_id, actor_label="patient_self_service")
    return RedirectResponse("/patient/appointments", status_code=303)


@router.get("/appointments/{appointment_id}/reschedule")
def reschedule_form(
    request: Request, appointment_id: int, user: User = Depends(require_patient), db: Session = Depends(get_db)
):
    appt = db.get(Appointment, appointment_id)
    if not appt:
        return RedirectResponse("/patient/appointments", status_code=303)
    assert_owns_patient(user, appt.patient_id)
    slots = appointment_tools.get_available_slots(db, appt.doctor.department_id)
    return templates.TemplateResponse(
        "patient/reschedule.html", {"request": request, "user": user, "appointment": appt, "slots": slots}
    )


@router.post("/appointments/{appointment_id}/reschedule")
def reschedule_submit(
    appointment_id: int,
    new_slot_id: int = Form(...),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    appt = db.get(Appointment, appointment_id)
    if appt:
        assert_owns_patient(user, appt.patient_id)
        appointment_tools.reschedule_appointment(db, appointment_id, new_slot_id, actor_label="patient_self_service")
    return RedirectResponse("/patient/appointments", status_code=303)


@router.get("/profile")
def profile_form(request: Request, user: User = Depends(require_patient)):
    return templates.TemplateResponse("patient/profile.html", {"request": request, "user": user, "saved": False})


@router.post("/profile")
def profile_update(
    request: Request,
    phone: str = Form(""),
    date_of_birth: str = Form(""),
    preferred_language: str = Form("en"),
    emergency_contact: str = Form(""),
    user: User = Depends(require_patient),
    db: Session = Depends(get_db),
):
    profile = user.patient_profile
    profile.phone = phone or None
    profile.date_of_birth = date_of_birth or None
    profile.preferred_language = preferred_language or "en"
    profile.emergency_contact = emergency_contact or None
    db.commit()
    log_event(db, "patient_self_service", "profile_updated", "PatientProfile", profile.id, actor_id=user.id)
    return templates.TemplateResponse("patient/profile.html", {"request": request, "user": user, "saved": True})


@router.get("/documents")
def documents_list(request: Request, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    profile = user.patient_profile
    documents = db.query(PatientDocument).filter_by(patient_id=profile.id).order_by(PatientDocument.created_at.desc()).all()
    return templates.TemplateResponse(
        "patient/documents.html", {"request": request, "user": user, "documents": documents}
    )


@router.get("/reminders")
def reminders_list(request: Request, user: User = Depends(require_patient), db: Session = Depends(get_db)):
    profile = user.patient_profile
    reminders = db.query(Reminder).filter_by(patient_id=profile.id).order_by(Reminder.scheduled_at.asc()).all()
    return templates.TemplateResponse(
        "patient/reminders.html", {"request": request, "user": user, "reminders": reminders}
    )
