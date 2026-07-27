"""Synthetic seed data — no real patient data. Populates departments, doctors, appointment
slots, a demo staff account, and a demo patient account so the app is usable immediately
after setup. Safe to re-run (idempotent)."""
from datetime import datetime, timedelta, timezone

from app.auth import hash_password
from app.database import init_db, session_scope
from app.models import (
    AppointmentSlot,
    Department,
    Doctor,
    PatientProfile,
    SlotStatus,
    User,
    UserRole,
)

DEPARTMENTS = [
    {
        "name": "Cardiology",
        "description": "Heart and cardiovascular care follow-ups and consultations.",
        "required_documents": "ecg,blood_report",
    },
    {
        "name": "Orthopedics",
        "description": "Bones, joints, and musculoskeletal administrative visits.",
        "required_documents": "xray",
    },
    {
        "name": "General Medicine",
        "description": "General consultations and administrative check-ins.",
        "required_documents": "",
    },
    {
        "name": "Dermatology",
        "description": "Skin-related consultations.",
        "required_documents": "photo_report",
    },
    {
        "name": "Pediatrics",
        "description": "Child health administrative visits.",
        "required_documents": "",
    },
    {
        "name": "Emergency",
        "description": "Not for self-scheduling — emergencies are escalated to staff immediately.",
        "required_documents": "",
    },
]

DOCTORS = {
    "Cardiology": ["Dr. Asha Mehta", "Dr. Ravi Kulkarni"],
    "Orthopedics": ["Dr. Neha Sharma"],
    "General Medicine": ["Dr. Farah Khan", "Dr. Sameer Iyer"],
    "Dermatology": ["Dr. Priya Nair"],
    "Pediatrics": ["Dr. Anil Bose"],
    "Emergency": ["Dr. On-Call Duty Physician"],
}


def run_seed():
    init_db()
    with session_scope() as db:
        # --- Departments ---
        dept_map = {}
        for d in DEPARTMENTS:
            existing = db.query(Department).filter_by(name=d["name"]).first()
            if not existing:
                existing = Department(**d)
                db.add(existing)
                db.flush()
            dept_map[d["name"]] = existing

        # --- Doctors + future slots ---
        for dept_name, doctor_names in DOCTORS.items():
            dept = dept_map[dept_name]
            for doc_name in doctor_names:
                doctor = db.query(Doctor).filter_by(name=doc_name, department_id=dept.id).first()
                if not doctor:
                    doctor = Doctor(name=doc_name, department_id=dept.id, active=True)
                    db.add(doctor)
                    db.flush()

                existing_slots = db.query(AppointmentSlot).filter_by(doctor_id=doctor.id).count()
                if existing_slots == 0:
                    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
                    for day_offset in range(5):
                        for hour_offset in (9, 11, 14, 16):
                            start = base + timedelta(days=day_offset, hours=hour_offset - base.hour)
                            slot = AppointmentSlot(
                                doctor_id=doctor.id,
                                start_time=start,
                                end_time=start + timedelta(minutes=30),
                                status=SlotStatus.available,
                            )
                            db.add(slot)

        # --- Demo staff account ---
        staff_email = "staff@agentcare.demo"
        if not db.query(User).filter_by(email=staff_email).first():
            staff = User(
                name="Staff Admin",
                email=staff_email,
                password_hash=hash_password("StaffPass123!"),
                role=UserRole.staff,
            )
            db.add(staff)

        # --- Demo patient account ---
        patient_email = "patient@agentcare.demo"
        patient_user = db.query(User).filter_by(email=patient_email).first()
        if not patient_user:
            patient_user = User(
                name="Demo Patient",
                email=patient_email,
                password_hash=hash_password("PatientPass123!"),
                role=UserRole.patient,
            )
            db.add(patient_user)
            db.flush()
            profile = PatientProfile(
                user_id=patient_user.id,
                date_of_birth="1990-04-12",
                phone="+91-9000000000",
                preferred_language="en",
                emergency_contact="+91-9111111111",
            )
            db.add(profile)

    print("Seed complete.")
    print("Staff login:   staff@agentcare.demo / StaffPass123!")
    print("Patient login: patient@agentcare.demo / PatientPass123!")


if __name__ == "__main__":
    run_seed()
