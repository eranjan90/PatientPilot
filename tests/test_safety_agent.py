"""Proves the safety boundary is enforced in code: emergency/diagnosis/prescription
language is blocked and escalated WITHOUT ever reaching the LLM (deterministic screen runs
first), so this test needs no API key."""
from app.agents.safety_agent import SafetyAgent
from app.models import Escalation, WorkflowRun, WorkflowStatus


def _make_run(db_session, seeded):
    run = WorkflowRun(patient_id=seeded["patient_profile"].id, current_step="intake", status=WorkflowStatus.in_progress)
    db_session.add(run)
    db_session.commit()
    return run


def test_emergency_request_is_escalated_as_emergency(db_session, seeded):
    run = _make_run(db_session, seeded)
    agent = SafetyAgent()
    result = agent.run(db_session, run.id, "I think I'm having a heart attack, chest pain right now")
    assert result["safe"] is False
    assert result["escalated"] is True

    escalation = db_session.query(Escalation).filter_by(workflow_run_id=run.id).first()
    assert escalation is not None
    assert escalation.severity == "emergency"

    db_session.refresh(run)
    assert run.status == WorkflowStatus.escalated


def test_diagnosis_request_is_escalated(db_session, seeded):
    run = _make_run(db_session, seeded)
    agent = SafetyAgent()
    result = agent.run(db_session, run.id, "Can you diagnose what disease do i have based on my symptoms")
    assert result["safe"] is False
    escalation = db_session.query(Escalation).filter_by(workflow_run_id=run.id).first()
    assert escalation.severity == "normal"


def test_prescription_request_is_escalated(db_session, seeded):
    run = _make_run(db_session, seeded)
    agent = SafetyAgent()
    result = agent.run(db_session, run.id, "What dosage of medication should I take, please prescribe something")
    assert result["safe"] is False


def test_administrative_request_does_not_trigger_deterministic_block(db_session, seeded):
    agent = SafetyAgent()
    # Only checking the deterministic layer here (no network/LLM call involved).
    hit = agent.deterministic_screen("I'd like to book a cardiology follow-up appointment next week")
    assert hit is None
