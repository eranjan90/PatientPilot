"""Appointment Agent.

Distinct responsibility: finds available slots, checks conflicts, and books/reschedules/
cancels appointments — all persisted immediately to the database. Its tool set only touches
scheduling data, nothing clinical.
"""
from app.llm import ToolRegistry, run_tool_loop
from app.tools import appointment_tools

SLOTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_available_slots",
        "description": "List upcoming available appointment slots for a department.",
        "parameters": {
            "type": "object",
            "properties": {
                "department_id": {"type": "integer"},
                "doctor_id": {"type": "integer"},
            },
            "required": ["department_id"],
        },
    },
}

BOOK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "book_appointment",
        "description": "Book a specific available slot for the patient.",
        "parameters": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["slot_id"],
        },
    },
}

LIST_MY_APPTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_my_appointments",
        "description": "List this patient's current active appointments (needed before a reschedule).",
        "parameters": {"type": "object", "properties": {}},
    },
}

RESCHEDULE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reschedule_appointment",
        "description": "Move an existing appointment to a new available slot.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer"},
                "new_slot_id": {"type": "integer"},
            },
            "required": ["appointment_id", "new_slot_id"],
        },
    },
}

SYSTEM_PROMPT = """You are the Appointment Agent for AgentCare. Your job is purely
administrative scheduling. You will be told the patient's intent_type.

If intent_type is "new_appointment" (or unclear): call `get_available_slots` to see real open
slots, choose the earliest one that reasonably matches the patient's stated timing preference
(e.g. "next week"), then call `book_appointment` with that slot_id and a short non-clinical
reason (e.g. "cardiology follow-up", never a diagnosis).

If intent_type is "reschedule": call `list_my_appointments` first to find the existing
appointment for this department, then `get_available_slots` for a new time, then call
`reschedule_appointment` with that appointment_id and the new slot_id.

Finish with a short JSON summary: {"booked": true/false, "appointment_id": <id or null>}."""


class AppointmentAgent:
    name = "appointment_agent"

    def run(self, db, patient_id: int, department_id: int, patient_request_text: str, intent_type: str = "new_appointment") -> dict:
        registry = ToolRegistry()
        registry.add(
            SLOTS_SCHEMA,
            lambda department_id=department_id, doctor_id=None: appointment_tools.get_available_slots(
                db, department_id=department_id, doctor_id=doctor_id
            ),
        )

        def book(slot_id: int, reason: str = ""):
            return appointment_tools.book_appointment(db, patient_id, slot_id, reason, actor_label=self.name)

        def list_my_appointments():
            return appointment_tools.list_patient_appointments(db, patient_id)

        def reschedule(appointment_id: int, new_slot_id: int):
            return appointment_tools.reschedule_appointment(db, appointment_id, new_slot_id, actor_label=self.name)

        registry.add(BOOK_SCHEMA, book)
        registry.add(LIST_MY_APPTS_SCHEMA, list_my_appointments)
        registry.add(RESCHEDULE_SCHEMA, reschedule)

        prompt = f"intent_type: {intent_type}\nDepartment ID: {department_id}\nPatient request: {patient_request_text}"
        outcome = run_tool_loop(SYSTEM_PROMPT, prompt, registry)

        for call in outcome["tool_calls"]:
            if call["tool"] == "book_appointment" and call["result"].get("success"):
                return {"booked": True, "action": "booked", **call["result"]}
            if call["tool"] == "reschedule_appointment" and call["result"].get("success"):
                return {"booked": True, "action": "rescheduled", **call["result"]}

        return {"booked": False, "error": outcome["final_text"]}
