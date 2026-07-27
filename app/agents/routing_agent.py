"""Department Routing Agent.

Distinct responsibility: classifies the administrative intent of a request and maps it to a
real Department row. Its tool set is restricted to department lookup + escalation — it cannot
book appointments, touch documents, or do anything clinical. If it can't confidently match a
department, it escalates rather than guessing.
"""
from app.llm import ToolRegistry, run_tool_loop
from app.tools import department_tools, escalation_tools

LIST_DEPTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_departments",
        "description": "List all active hospital departments available for routing.",
        "parameters": {"type": "object", "properties": {}},
    },
}

CLASSIFY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "classify_department",
        "description": "Attempt to match a department name guess against real hospital departments.",
        "parameters": {
            "type": "object",
            "properties": {"department_name_guess": {"type": "string"}},
            "required": ["department_name_guess"],
        },
    },
}

ESCALATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate_routing",
        "description": "Escalate when the correct department is unclear or unsupported.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
}

SYSTEM_PROMPT = """You are the Department Routing Agent for AgentCare. Your ONLY job is
administrative routing: read the patient's request and map it to the correct hospital
department using the tools provided. Call `list_departments` if you need to see the options,
then call `classify_department` with your best department name guess. Never describe or imply
a medical diagnosis when choosing a department (e.g. route a heart follow-up to Cardiology
without asserting what condition the patient has). If `classify_department` returns no match,
or the request is clearly an emergency/unsupported case, call `escalate_routing` with a reason
instead of guessing. When you have a confirmed department, reply with a short JSON:
{"department_id": <id>, "department_name": "<name>"}."""


class DepartmentRoutingAgent:
    name = "routing_agent"

    def run(self, db, workflow_run_id: int, patient_request_text: str) -> dict:
        registry = ToolRegistry()

        registry.add(LIST_DEPTS_SCHEMA, lambda: department_tools.list_departments(db))
        registry.add(
            CLASSIFY_SCHEMA,
            lambda department_name_guess: department_tools.classify_department(
                db, department_name_guess, actor_label=self.name
            ),
        )

        def escalate_routing(reason: str):
            return escalation_tools.create_escalation(db, workflow_run_id, reason, severity="normal", actor_label=self.name)

        registry.add(ESCALATE_SCHEMA, escalate_routing)

        outcome = run_tool_loop(SYSTEM_PROMPT, patient_request_text, registry)

        for call in outcome["tool_calls"]:
            if call["tool"] == "escalate_routing":
                return {"routed": False, "escalated": True, "reason": call["args"].get("reason")}
            if call["tool"] == "classify_department" and call["result"]:
                return {"routed": True, "escalated": False, **call["result"]}

        return {"routed": False, "escalated": True, "reason": "Routing agent could not determine a department"}
