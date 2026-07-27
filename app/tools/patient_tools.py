"""Patient record tool — find-or-create logic backed by the real database.
Used by the Coordinator Agent to identify or create the patient record (workflow step 1)."""
from sqlalchemy.orm import Session

from app.models import PatientProfile, User, UserRole
from app.tools.audit_tools import log_event


def find_patient_by_email(db: Session, email: str) -> PatientProfile | None:
    user = db.query(User).filter_by(email=email.strip().lower()).first()
    if not user or not user.patient_profile:
        return None
    return user.patient_profile


def get_patient(db: Session, patient_id: int) -> PatientProfile | None:
    return db.get(PatientProfile, patient_id)


def find_or_create_patient(
    db: Session,
    name: str,
    email: str,
    password_hash: str | None = None,
    date_of_birth: str | None = None,
    phone: str | None = None,
    actor_label: str = "coordinator_agent",
) -> dict:
    """Returns a dict describing the patient record and whether it was newly created.
    Real DB read/write — not a stub."""
    email_norm = email.strip().lower()
    existing = find_patient_by_email(db, email_norm)
    if existing:
        log_event(db, actor_label, "patient_lookup_found", "PatientProfile", existing.id)
        return {"patient_id": existing.id, "user_id": existing.user_id, "created": False, "name": existing.user.name}

    user = User(
        name=name,
        email=email_norm,
        password_hash=password_hash or "unset",
        role=UserRole.patient,
    )
    db.add(user)
    db.flush()
    profile = PatientProfile(user_id=user.id, date_of_birth=date_of_birth, phone=phone)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    log_event(db, actor_label, "patient_created", "PatientProfile", profile.id, actor_id=user.id)
    return {"patient_id": profile.id, "user_id": user.id, "created": True, "name": user.name}
