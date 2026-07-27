"""Department lookup/classification tool. Maps free-text requests to a real, persisted
Department row — pure administrative routing, never a clinical judgment."""
from sqlalchemy.orm import Session

from app.models import Department
from app.tools.audit_tools import log_event


def list_departments(db: Session) -> list[dict]:
    depts = db.query(Department).filter_by(active=True).all()
    return [{"id": d.id, "name": d.name, "description": d.description} for d in depts]


def classify_department(db: Session, department_name_guess: str, actor_label: str = "routing_agent") -> dict | None:
    """Fuzzy-matches a department name guess (produced by the LLM) against real Department
    rows. Returns None if no confident match — caller should then escalate instead of guessing."""
    guess = department_name_guess.strip().lower()
    depts = db.query(Department).filter_by(active=True).all()

    # exact match first
    for d in depts:
        if d.name.lower() == guess:
            log_event(db, actor_label, "department_matched_exact", "Department", d.id, metadata={"guess": guess})
            return {"id": d.id, "name": d.name, "required_documents": d.required_documents}

    # substring / contains match
    for d in depts:
        if guess in d.name.lower() or d.name.lower() in guess:
            log_event(db, actor_label, "department_matched_fuzzy", "Department", d.id, metadata={"guess": guess})
            return {"id": d.id, "name": d.name, "required_documents": d.required_documents}

    log_event(db, actor_label, "department_match_failed", "Department", None, metadata={"guess": guess})
    return None


def get_required_documents(db: Session, department_id: int) -> list[str]:
    dept = db.get(Department, department_id)
    if not dept or not dept.required_documents:
        return []
    return [x.strip() for x in dept.required_documents.split(",") if x.strip()]
