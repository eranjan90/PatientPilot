"""End-to-end test of the full Coordinator -> Safety -> Routing -> Appointment -> Document ->
Follow-up pipeline. The LLM reasoning steps are replaced with scripted stand-ins (no network
call, no API key needed) so the test is deterministic in CI, but every tool call underneath
still runs against the real in-memory database — proving the wiring end to end: agent -> tool
-> database -> persisted result."""
import json
from types import SimpleNamespace

from app.agents import appointment_agent as appt_mod
from app.agents import coordinator as coord_mod
from app.agents import document_agent as doc_mod
from app.agents import followup_agent as follow_mod
from app.agents import routing_agent as routing_mod
from app.agents import safety_agent as safety_mod
from app.models import Appointment, AuditEvent, Reminder, WorkflowRun, WorkflowStatus


def fake_intent_response(*args, **kwargs):
    payload = {
        "intent_type": "new_appointment",
        "department_guess": "Cardiology",
        "timing_preference": "next week",
        "mentions_documents": True,
        "notes": "Cardiology follow-up appointment request",
    }
    message = SimpleNamespace(content=json.dumps(payload))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_safety_loop(system_prompt, user_message, registry, max_iterations=6):
    return {"final_text": "SAFE", "tool_calls": [], "messages": []}


def fake_routing_loop(system_prompt, user_message, registry, max_iterations=6):
    result = registry.functions["classify_department"](department_name_guess="Cardiology")
    return {
        "final_text": json.dumps(result),
        "tool_calls": [{"tool": "classify_department", "args": {"department_name_guess": "Cardiology"}, "result": result}],
        "messages": [],
    }


def fake_appointment_loop(system_prompt, user_message, registry, max_iterations=6):
    slots = registry.functions["get_available_slots"]()
    assert slots, "expected at least one available slot from the real tool"
    booked = registry.functions["book_appointment"](slot_id=slots[0]["slot_id"], reason="cardiology follow-up")
    return {
        "final_text": json.dumps({"booked": booked.get("success")}),
        "tool_calls": [{"tool": "book_appointment", "args": {}, "result": booked}],
        "messages": [],
    }


def fake_document_loop(system_prompt, user_message, registry, max_iterations=6):
    result = registry.functions["check_missing_documents"]()
    return {
        "final_text": "Document check complete",
        "tool_calls": [{"tool": "check_missing_documents", "args": {}, "result": result}],
        "messages": [],
    }


def fake_followup_loop(system_prompt, user_message, registry, max_iterations=6):
    r1 = registry.functions["create_reminder"](scheduled_at="2026-08-01T09:00:00", reminder_type="appointment_reminder")
    r2 = registry.functions["create_reminder"](scheduled_at="2026-08-04T09:00:00", reminder_type="follow_up")
    registry.functions["dispatch_notification"](reminder_id=r1["reminder_id"])
    return {
        "final_text": "Reminders scheduled",
        "tool_calls": [
            {"tool": "create_reminder", "args": {}, "result": r1},
            {"tool": "create_reminder", "args": {}, "result": r2},
        ],
        "messages": [],
    }


def test_full_happy_path_books_appointment_and_schedules_followup(db_session, seeded, monkeypatch):
    monkeypatch.setattr(coord_mod, "chat_completion_with_retry", fake_intent_response)
    monkeypatch.setattr(safety_mod, "run_tool_loop", fake_safety_loop)
    monkeypatch.setattr(routing_mod, "run_tool_loop", fake_routing_loop)
    monkeypatch.setattr(appt_mod, "run_tool_loop", fake_appointment_loop)
    monkeypatch.setattr(doc_mod, "run_tool_loop", fake_document_loop)
    monkeypatch.setattr(follow_mod, "run_tool_loop", fake_followup_loop)

    coordinator = coord_mod.CoordinatorAgent()
    patient_id = seeded["patient_profile"].id

    result = coordinator.run(
        db_session,
        patient_id,
        "I need a cardiology follow-up next week and want to attach my old ECG.",
        uploaded_files=[("old_ecg.pdf", b"ECG waveform sample data")],
    )

    assert result["status"] == "completed"
    assert result["safety"]["safe"] is True
    assert result["routing"]["routed"] is True
    assert result["appointment"]["booked"] is True
    assert "missing" in result["documents"]
    assert result["followup"]["reminders_created"]

    # --- persistence checks: everything must actually be in the database ---
    run = db_session.get(WorkflowRun, result["workflow_run_id"])
    assert run.status == WorkflowStatus.completed
    assert run.current_step == "followup_scheduled"

    appt = db_session.query(Appointment).filter_by(patient_id=patient_id).first()
    assert appt is not None

    reminders = db_session.query(Reminder).filter_by(patient_id=patient_id).all()
    assert len(reminders) == 2

    audit_events = db_session.query(AuditEvent).all()
    assert len(audit_events) > 3  # booking, reminders, workflow steps all logged


def test_emergency_request_halts_pipeline_before_booking(db_session, seeded, monkeypatch):
    monkeypatch.setattr(coord_mod, "chat_completion_with_retry", fake_intent_response)
    # deliberately do NOT patch safety_agent's run_tool_loop — the deterministic keyword
    # screen should catch this before any LLM call happens.
    monkeypatch.setattr(routing_mod, "run_tool_loop", fake_routing_loop)
    monkeypatch.setattr(appt_mod, "run_tool_loop", fake_appointment_loop)

    coordinator = coord_mod.CoordinatorAgent()
    patient_id = seeded["patient_profile"].id

    result = coordinator.run(db_session, patient_id, "I have severe chest pain and can't breathe")

    assert result["status"] == "escalated"
    assert "appointment" not in result  # pipeline must stop before booking anything

    appt_count = db_session.query(Appointment).filter_by(patient_id=patient_id).count()
    assert appt_count == 0
