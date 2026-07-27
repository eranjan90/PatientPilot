"""Escalation/approval tool — the Safety & Escalation Agent's core mechanism for handing
uncertain, emergency, or sensitive requests to a human instead of letting the AI decide."""
from sqlalchemy.orm import Session

from app.models import Escalation, EscalationStatus, WorkflowRun, WorkflowStatus
from app.tools.audit_tools import log_event


def create_escalation(
    db: Session,
    workflow_run_id: int,
    reason: str,
    severity: str = "normal",
    actor_label: str = "safety_agent",
) -> dict:
    escalation = Escalation(
        workflow_run_id=workflow_run_id,
        reason=reason,
        severity=severity,
        status=EscalationStatus.pending,
    )
    db.add(escalation)

    run = db.get(WorkflowRun, workflow_run_id)
    if run:
        run.status = WorkflowStatus.escalated

    db.commit()
    db.refresh(escalation)
    log_event(
        db, actor_label, "escalation_created", "Escalation", escalation.id,
        metadata={"reason": reason, "severity": severity, "workflow_run_id": workflow_run_id},
    )
    return {"escalation_id": escalation.id, "severity": severity, "status": "pending"}


def list_pending_escalations(db: Session) -> list[dict]:
    items = db.query(Escalation).filter_by(status=EscalationStatus.pending).order_by(Escalation.created_at.desc()).all()
    return [
        {
            "id": e.id,
            "workflow_run_id": e.workflow_run_id,
            "reason": e.reason,
            "severity": e.severity,
            "created_at": e.created_at.isoformat(),
        }
        for e in items
    ]


def review_escalation(
    db: Session, escalation_id: int, approve: bool, reviewer_id: int, notes: str | None = None
) -> dict:
    escalation = db.get(Escalation, escalation_id)
    if not escalation:
        return {"success": False, "error": "Escalation not found"}

    escalation.status = EscalationStatus.approved if approve else EscalationStatus.rejected
    escalation.reviewed_by = reviewer_id
    escalation.review_notes = notes

    run = db.get(WorkflowRun, escalation.workflow_run_id)
    if run:
        run.status = WorkflowStatus.in_progress if approve else WorkflowStatus.failed

    db.commit()
    log_event(
        db, "staff_review", "escalation_reviewed", "Escalation", escalation.id,
        actor_id=reviewer_id, metadata={"approved": approve, "notes": notes},
    )
    return {"success": True, "escalation_id": escalation.id, "status": escalation.status.value}
