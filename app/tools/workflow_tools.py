"""Workflow state persistence tool. WorkflowRun.state is the JSON blob agents read from and
write to as they hand off work — this is what makes workflow state durable across restarts
instead of living only in memory."""
import json

from sqlalchemy.orm import Session

from app.models import WorkflowRun, WorkflowStatus
from app.tools.audit_tools import log_event


def create_workflow_run(db: Session, patient_id: int, original_request: str, actor_label: str = "coordinator_agent") -> WorkflowRun:
    run = WorkflowRun(
        patient_id=patient_id,
        current_step="intake",
        state=json.dumps({}),
        status=WorkflowStatus.in_progress,
        original_request=original_request,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    log_event(db, actor_label, "workflow_started", "WorkflowRun", run.id, metadata={"patient_id": patient_id})
    return run


def get_workflow_state(db: Session, run_id: int) -> dict:
    run = db.get(WorkflowRun, run_id)
    if not run:
        return {}
    return json.loads(run.state or "{}")


def update_workflow_state(
    db: Session, run_id: int, step: str, patch: dict, actor_label: str = "coordinator_agent"
) -> dict:
    run = db.get(WorkflowRun, run_id)
    if not run:
        return {}
    state = json.loads(run.state or "{}")
    state.update(patch)
    run.state = json.dumps(state, default=str)
    run.current_step = step
    db.commit()
    log_event(db, actor_label, f"workflow_step_{step}", "WorkflowRun", run.id, metadata=patch)
    return state


def set_workflow_status(db: Session, run_id: int, status: WorkflowStatus, actor_label: str = "coordinator_agent") -> None:
    run = db.get(WorkflowRun, run_id)
    if not run:
        return
    run.status = status
    db.commit()
    log_event(db, actor_label, f"workflow_{status.value}", "WorkflowRun", run.id)
