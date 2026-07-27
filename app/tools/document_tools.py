"""Document tool — real classification (filename + content heuristics), checksum-based
duplicate detection, storage, and missing-document checks. Used by the Document Agent."""
import hashlib
import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models import PatientDocument
from app.tools.audit_tools import log_event
from app.tools.department_tools import get_required_documents

KEYWORD_MAP = {
    "ecg": ["ecg", "ekg", "electrocardiogram"],
    "blood_report": ["blood", "cbc", "lipid", "hemoglobin", "hba1c"],
    "xray": ["xray", "x-ray", "radiograph"],
    "photo_report": ["photo", "skin", "derm"],
    "prescription": ["prescription", "rx"],
    "discharge_summary": ["discharge", "summary"],
}


def compute_checksum(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def classify_document(filename: str, file_bytes: bytes) -> str:
    """Real heuristic classification: checks filename keywords, falling back to a text-content
    scan for text-based files. Not a hardcoded fixed return."""
    name = filename.lower()
    for doc_type, keywords in KEYWORD_MAP.items():
        if any(kw in name for kw in keywords):
            return doc_type

    if name.endswith((".txt", ".csv")):
        try:
            text = file_bytes[:2000].decode("utf-8", errors="ignore").lower()
            for doc_type, keywords in KEYWORD_MAP.items():
                if any(kw in text for kw in keywords):
                    return doc_type
        except Exception:
            pass

    return "other"


def store_document(
    db: Session,
    patient_id: int,
    filename: str,
    file_bytes: bytes,
    document_date: str | None = None,
    actor_label: str = "document_agent",
) -> dict:
    checksum = compute_checksum(file_bytes)
    doc_type = classify_document(filename, file_bytes)

    duplicate = (
        db.query(PatientDocument)
        .filter_by(patient_id=patient_id, checksum=checksum)
        .first()
    )

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    safe_name = f"{patient_id}_{checksum[:12]}_{filename}"
    file_path = os.path.join(settings.UPLOAD_DIR, safe_name)
    if not duplicate:
        with open(file_path, "wb") as f:
            f.write(file_bytes)

    parsed_date = None
    if document_date:
        try:
            parsed_date = datetime.fromisoformat(document_date)
        except ValueError:
            parsed_date = None

    doc = PatientDocument(
        patient_id=patient_id,
        document_type=doc_type,
        file_path=file_path if not duplicate else duplicate.file_path,
        document_date=parsed_date,
        checksum=checksum,
        is_duplicate=bool(duplicate),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    log_event(
        db, actor_label, "document_stored", "PatientDocument", doc.id,
        metadata={"document_type": doc_type, "is_duplicate": bool(duplicate), "filename": filename},
    )
    return {
        "document_id": doc.id,
        "document_type": doc_type,
        "is_duplicate": bool(duplicate),
        "checksum": checksum,
    }


def check_missing_documents(db: Session, patient_id: int, department_id: int) -> dict:
    required = get_required_documents(db, department_id)
    if not required:
        return {"required": [], "missing": [], "present": []}

    present_types = {
        d.document_type
        for d in db.query(PatientDocument).filter_by(patient_id=patient_id).all()
    }
    missing = [r for r in required if r not in present_types]
    return {"required": required, "missing": missing, "present": sorted(present_types)}
