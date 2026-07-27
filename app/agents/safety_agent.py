"""Safety & Escalation Agent.

Distinct responsibility: screens every incoming request for emergencies, diagnosis requests,
and prescription/dosage requests, and creates a persisted Escalation for human review when
triggered. It NEVER diagnoses, prescribes, or advises on treatment — its own tool set only
allows it to escalate, nothing clinical.

Defense in depth: a deterministic keyword screen runs first (cannot be bypassed by prompt
injection since it doesn't depend on the LLM at all), followed by an LLM pass for softer/
ambiguous cases that also only has access to the escalate tool.
"""
from app.llm import ToolRegistry, run_tool_loop
from app.tools import escalation_tools

EMERGENCY_KEYWORDS = [
    "chest pain", "can't breathe", "cannot breathe", "unconscious", "suicidal",
    "suicide", "severe bleeding", "overdose", "heart attack", "stroke", "not breathing",
    "unresponsive", "seizure",
]
DIAGNOSIS_KEYWORDS = [
    "diagnose", "diagnosis", "what disease do i have", "do i have cancer",
    "what's wrong with me", "what illness", "is it serious",
]
PRESCRIPTION_KEYWORDS = [
    "prescribe", "prescription for", "what medicine should i take", "dosage",
    "how many mg", "increase my dose", "change my medication", "stop taking my",
]

ESCALATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": "Create a human-review escalation record for this workflow run.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why this needs human review"},
                "severity": {"type": "string", "enum": ["normal", "emergency"]},
            },
            "required": ["reason"],
        },
    },
}

SYSTEM_PROMPT = """You are the Safety & Escalation Agent for AgentCare, a hospital ADMINISTRATIVE
assistant. You screen patient requests for: medical emergencies, requests asking the system to
diagnose a condition, requests asking the system to prescribe or change medication/dosage, or
other situations a human clinician/staff member must review.

You must NEVER diagnose a condition, suggest a treatment, or recommend a medicine or dosage —
not even a general suggestion. If the request touches any of those areas, call the `escalate`
tool with a clear reason and appropriate severity. If the request is purely administrative
(booking, documents, rescheduling, general info) and has no clinical or emergency content,
do not call any tool and simply reply with the single word SAFE."""


class SafetyAgent:
    name = "safety_agent"

    def deterministic_screen(self, text: str) -> dict | None:
        lower = text.lower()
        for kw in EMERGENCY_KEYWORDS:
            if kw in lower:
                return {"severity": "emergency", "reason": f"Emergency language detected: '{kw}'"}
        for kw in DIAGNOSIS_KEYWORDS:
            if kw in lower:
                return {"severity": "normal", "reason": f"Request appears to ask for a diagnosis: '{kw}'"}
        for kw in PRESCRIPTION_KEYWORDS:
            if kw in lower:
                return {"severity": "normal", "reason": f"Request appears to ask for prescription/dosage guidance: '{kw}'"}
        return None

    def run(self, db, workflow_run_id: int, patient_request_text: str) -> dict:
        hit = self.deterministic_screen(patient_request_text)
        if hit:
            result = escalation_tools.create_escalation(
                db, workflow_run_id, hit["reason"], severity=hit["severity"], actor_label=self.name
            )
            return {"safe": False, "escalated": True, "reason": hit["reason"], **result}

        registry = ToolRegistry()

        def escalate(reason: str, severity: str = "normal"):
            return escalation_tools.create_escalation(
                db, workflow_run_id, reason, severity=severity, actor_label=self.name
            )

        registry.add(ESCALATE_SCHEMA, escalate)

        outcome = run_tool_loop(SYSTEM_PROMPT, patient_request_text, registry)
        if outcome["tool_calls"]:
            escalate_call = outcome["tool_calls"][-1]
            return {
                "safe": False,
                "escalated": True,
                "reason": escalate_call["args"].get("reason", "Flagged by safety agent"),
                **escalate_call["result"],
            }
        return {"safe": True, "escalated": False, "reason": outcome["final_text"]}
