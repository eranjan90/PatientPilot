"""Appointment tool — real slot availability, conflict checking, booking, reschedule, and
cancel logic against the persisted database. This is the Appointment Agent's tool set."""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Appointment, AppointmentSlot, AppointmentStatus, Doctor, SlotStatus
from app.tools.audit_tools import log_event


def get_available_slots(
    db: Session,
    department_id: int,
    doctor_id: int | None = None,
    limit: int = 10,
) -> list[dict]:
    query = (
        db.query(AppointmentSlot)
        .join(Doctor, AppointmentSlot.doctor_id == Doctor.id)
        .filter(Doctor.department_id == department_id)
        .filter(AppointmentSlot.status == SlotStatus.available)
        .filter(AppointmentSlot.start_time > datetime.utcnow())
        .order_by(AppointmentSlot.start_time.asc())
        .limit(limit)
    )
    slots = query.all()
    return [
        {
            "slot_id": s.id,
            "doctor_id": s.doctor_id,
            "doctor_name": s.doctor.name,
            "start_time": s.start_time.isoformat(),
            "end_time": s.end_time.isoformat(),
        }
        for s in slots
    ]


def list_patient_appointments(db: Session, patient_id: int, active_only: bool = True) -> list[dict]:
    query = db.query(Appointment).filter(Appointment.patient_id == patient_id)
    if active_only:
        query = query.filter(Appointment.status.in_([AppointmentStatus.pending, AppointmentStatus.confirmed, AppointmentStatus.rescheduled]))
    appts = query.order_by(Appointment.created_at.desc()).all()
    return [
        {
            "appointment_id": a.id,
            "doctor_id": a.doctor_id,
            "doctor_name": a.doctor.name,
            "department_id": a.doctor.department_id,
            "start_time": a.slot.start_time.isoformat(),
            "status": a.status.value,
            "reason": a.reason,
        }
        for a in appts
    ]


def check_conflict(db: Session, patient_id: int, slot_id: int) -> bool:
    """True if the patient already has a confirmed/pending appointment overlapping this slot."""
    slot = db.get(AppointmentSlot, slot_id)
    if not slot:
        return False
    overlapping = (
        db.query(Appointment)
        .join(AppointmentSlot, Appointment.slot_id == AppointmentSlot.id)
        .filter(Appointment.patient_id == patient_id)
        .filter(Appointment.status.in_([AppointmentStatus.pending, AppointmentStatus.confirmed]))
        .filter(AppointmentSlot.start_time < slot.end_time)
        .filter(AppointmentSlot.end_time > slot.start_time)
        .first()
    )
    return overlapping is not None


def book_appointment(
    db: Session,
    patient_id: int,
    slot_id: int,
    reason: str = "",
    actor_label: str = "appointment_agent",
) -> dict:
    slot = db.get(AppointmentSlot, slot_id)
    if not slot or slot.status != SlotStatus.available:
        return {"success": False, "error": "Slot not available"}

    if check_conflict(db, patient_id, slot_id):
        return {"success": False, "error": "Patient already has an overlapping appointment"}

    appt = Appointment(
        patient_id=patient_id,
        doctor_id=slot.doctor_id,
        slot_id=slot.id,
        status=AppointmentStatus.confirmed,
        reason=reason,
    )
    slot.status = SlotStatus.booked
    db.add(appt)
    db.commit()
    db.refresh(appt)
    log_event(
        db, actor_label, "appointment_booked", "Appointment", appt.id,
        metadata={"patient_id": patient_id, "slot_id": slot_id, "reason": reason},
    )
    return {
        "success": True,
        "appointment_id": appt.id,
        "doctor_name": slot.doctor.name,
        "start_time": slot.start_time.isoformat(),
        "status": appt.status.value,
    }


def reschedule_appointment(
    db: Session, appointment_id: int, new_slot_id: int, actor_label: str = "appointment_agent"
) -> dict:
    appt = db.get(Appointment, appointment_id)
    if not appt:
        return {"success": False, "error": "Appointment not found"}
    new_slot = db.get(AppointmentSlot, new_slot_id)
    if not new_slot or new_slot.status != SlotStatus.available:
        return {"success": False, "error": "New slot not available"}

    old_slot = db.get(AppointmentSlot, appt.slot_id)
    if old_slot:
        old_slot.status = SlotStatus.available

    new_slot.status = SlotStatus.booked
    appt.slot_id = new_slot.id
    appt.doctor_id = new_slot.doctor_id
    appt.status = AppointmentStatus.rescheduled
    db.commit()
    db.refresh(appt)
    log_event(
        db, actor_label, "appointment_rescheduled", "Appointment", appt.id,
        metadata={"new_slot_id": new_slot_id},
    )
    return {"success": True, "appointment_id": appt.id, "new_start_time": new_slot.start_time.isoformat()}


def cancel_appointment(db: Session, appointment_id: int, actor_label: str = "appointment_agent") -> dict:
    appt = db.get(Appointment, appointment_id)
    if not appt:
        return {"success": False, "error": "Appointment not found"}
    slot = db.get(AppointmentSlot, appt.slot_id)
    if slot:
        slot.status = SlotStatus.available
    appt.status = AppointmentStatus.cancelled
    db.commit()
    log_event(db, actor_label, "appointment_cancelled", "Appointment", appt.id)
    return {"success": True, "appointment_id": appt.id}
