"""Reminder / notification tool used by the Follow-up Agent. Persists reminders and follow-up
tasks as real rows so they survive restarts and can be surfaced in the patient/staff UI.
Notification delivery is abstracted behind `dispatch_notification` — persisted as "sent" in
this demo build, swap in real SMTP/SMS provider calls without touching agent code."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Reminder, ReminderStatus
from app.tools.audit_tools import log_event


def create_reminder(
    db: Session,
    patient_id: int,
    scheduled_at: str,
    reminder_type: str = "appointment_reminder",
    appointment_id: int | None = None,
    notes: str | None = None,
    actor_label: str = "followup_agent",
) -> dict:
    try:
        scheduled_dt = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return {"success": False, "error": "scheduled_at must be an ISO datetime string"}

    reminder = Reminder(
        patient_id=patient_id,
        appointment_id=appointment_id,
        reminder_type=reminder_type,
        scheduled_at=scheduled_dt,
        notes=notes,
        status=ReminderStatus.scheduled,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    log_event(
        db, actor_label, "reminder_created", "Reminder", reminder.id,
        metadata={"reminder_type": reminder_type, "scheduled_at": scheduled_at},
    )
    return {"success": True, "reminder_id": reminder.id}


def dispatch_notification(db: Session, reminder_id: int, actor_label: str = "followup_agent") -> dict:
    """Marks a reminder as sent and logs it. In production this would call a real
    email/SMS provider; here it's a persisted, auditable state transition."""
    reminder = db.get(Reminder, reminder_id)
    if not reminder:
        return {"success": False, "error": "Reminder not found"}
    reminder.status = ReminderStatus.sent
    db.commit()
    log_event(db, actor_label, "notification_dispatched", "Reminder", reminder.id)
    return {"success": True, "reminder_id": reminder.id, "status": "sent"}


def list_upcoming_reminders(db: Session, patient_id: int) -> list[dict]:
    reminders = (
        db.query(Reminder)
        .filter_by(patient_id=patient_id)
        .order_by(Reminder.scheduled_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "type": r.reminder_type,
            "scheduled_at": r.scheduled_at.isoformat(),
            "status": r.status.value,
            "notes": r.notes,
        }
        for r in reminders
    ]
