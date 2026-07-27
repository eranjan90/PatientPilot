"""Follow-up Agent.

Distinct responsibility: creates appointment reminders and follow-up tasks, and dispatches
notifications. Its tool set only touches Reminder rows — it cannot rebook or cancel
appointments, and cannot touch documents.
"""
from datetime import datetime, timedelta

from app.llm import ToolRegistry, run_tool_loop
from app.tools import reminder_tools

CREATE_REMINDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_reminder",
        "description": "Create a reminder or follow-up task for the patient.",
        "parameters": {
            "type": "object",
            "properties": {
                "scheduled_at": {"type": "string", "description": "ISO datetime"},
                "reminder_type": {"type": "string", "enum": ["appointment_reminder", "follow_up"]},
                "notes": {"type": "string"},
            },
            "required": ["scheduled_at", "reminder_type"],
        },
    },
}

DISPATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dispatch_notification",
        "description": "Mark a reminder as sent (dispatch the notification).",
        "parameters": {
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
    },
}

SYSTEM_PROMPT = """You are the Follow-up Agent for AgentCare. Given a confirmed appointment
time, create an `appointment_reminder` for a sensible time before the visit (e.g. the day
before), and a `follow_up` reminder for a few days after the visit to check the workflow was
completed. Use the `create_reminder` tool for each, then call `dispatch_notification` on the
appointment_reminder to confirm delivery. Finish with a short JSON:
{"reminders_created": [<ids>]}."""


class FollowUpAgent:
    name = "followup_agent"

    def run(self, db, patient_id: int, appointment_id: int, appointment_start_iso: str) -> dict:
        registry = ToolRegistry()

        def create_reminder(scheduled_at: str, reminder_type: str, notes: str = ""):
            return reminder_tools.create_reminder(
                db, patient_id, scheduled_at, reminder_type, appointment_id, notes, actor_label=self.name
            )

        def dispatch(reminder_id: int):
            return reminder_tools.dispatch_notification(db, reminder_id, actor_label=self.name)

        registry.add(CREATE_REMINDER_SCHEMA, create_reminder)
        registry.add(DISPATCH_SCHEMA, dispatch)

        appt_start = datetime.fromisoformat(appointment_start_iso)
        suggested_reminder = (appt_start - timedelta(days=1)).isoformat()
        suggested_followup = (appt_start + timedelta(days=3)).isoformat()

        prompt = (
            f"Appointment ID {appointment_id} is confirmed for {appointment_start_iso}. "
            f"Suggested reminder time: {suggested_reminder}. Suggested follow-up check time: {suggested_followup}."
        )
        outcome = run_tool_loop(SYSTEM_PROMPT, prompt, registry)

        reminder_ids = [
            call["result"]["reminder_id"]
            for call in outcome["tool_calls"]
            if call["tool"] == "create_reminder" and call["result"].get("success")
        ]
        return {"reminders_created": reminder_ids, "summary": outcome["final_text"]}
