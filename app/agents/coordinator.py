"""Coordinator Agent.

Distinct responsibility: this is the only agent that sees the whole picture. It (1) uses its
own LLM prompt to extract structured administrative intent from the patient's free-text
request, (2) creates/loads the WorkflowRun and persists state after every hop, (3) delegates
to the Safety, Routing, Appointment, Document, and Follow-up agents in order, stopping early
on escalation, (4) combines their outputs into one confirmation built from persisted DB rows
(never invented text), and (5) marks the workflow completed or failed.
"""
from __future__ import annotations

import json
import logging

from app.agents.appointment_agent import AppointmentAgent
from app.agents.document_agent import DocumentAgent
from app.agents.followup_agent import FollowUpAgent
from app.agents.routing_agent import DepartmentRoutingAgent
from app.agents.safety_agent import SafetyAgent
from app.llm import LLMError, chat_completion_with_retry
from app.models import Appointment, WorkflowStatus
from app.tools import workflow_tools

logger = logging.getLogger("agentcare.coordinator")

INTENT_SYSTEM_PROMPT = """You are the Coordinator Agent for AgentCare, a hospital
ADMINISTRATIVE assistant (not a clinical one). Read the patient's free-text request and
extract structured administrative intent as compact JSON only, no prose, no markdown fences:
{"intent_type": "new_appointment" | "reschedule" | "cancel" | "document_only" | "general_inquiry",
 "department_guess": "<best-guess department name or empty string>",
 "timing_preference": "<free text like 'next week', 'as soon as possible', or empty>",
 "mentions_documents": true|false,
 "notes": "<one short administrative summary sentence, no diagnosis language>"}
Never include a diagnosis, medication, or dosage in any field."""


class CoordinatorAgent:
    name = "coordinator_agent"

    def __init__(self):
        self.safety_agent = SafetyAgent()
        self.routing_agent = DepartmentRoutingAgent()
        self.appointment_agent = AppointmentAgent()
        self.document_agent = DocumentAgent()
        self.followup_agent = FollowUpAgent()

    def extract_intent(self, request_text: str) -> dict:
        try:
            response = chat_completion_with_retry(
                messages=[
                    {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                    {"role": "user", "content": request_text},
                ],
                tools=None,
            )
            raw = response.choices[0].message.content or "{}"
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(raw)
        except (LLMError, json.JSONDecodeError) as exc:
            logger.warning("Intent extraction failed, falling back to defaults: %s", exc)
            return {
                "intent_type": "new_appointment",
                "department_guess": "",
                "timing_preference": "",
                "mentions_documents": False,
                "notes": request_text[:200],
            }

    def run(
        self,
        db,
        patient_id: int,
        request_text: str,
        uploaded_files: list[tuple[str, bytes]] | None = None,
    ) -> dict:
        run = workflow_tools.create_workflow_run(db, patient_id, request_text, actor_label=self.name)
        result: dict = {"workflow_run_id": run.id}

        try:
            intent = self.extract_intent(request_text)
            workflow_tools.update_workflow_state(db, run.id, "intent_extracted", {"intent": intent}, self.name)
            result["intent"] = intent

            # --- Safety Agent runs first, always ---
            safety_outcome = self.safety_agent.run(db, run.id, request_text)
            workflow_tools.update_workflow_state(db, run.id, "safety_checked", {"safety": safety_outcome}, self.name)
            result["safety"] = safety_outcome
            if not safety_outcome["safe"]:
                workflow_tools.set_workflow_status(db, run.id, WorkflowStatus.escalated, self.name)
                result["status"] = "escalated"
                return result

            # --- Department Routing Agent ---
            department_guess = intent.get("department_guess") or request_text
            routing_outcome = self.routing_agent.run(db, run.id, department_guess)
            workflow_tools.update_workflow_state(db, run.id, "department_routed", {"routing": routing_outcome}, self.name)
            result["routing"] = routing_outcome
            if not routing_outcome.get("routed"):
                workflow_tools.set_workflow_status(db, run.id, WorkflowStatus.escalated, self.name)
                result["status"] = "escalated"
                return result

            department_id = routing_outcome["id"]

            # --- Appointment Agent (skipped for pure document_only / general_inquiry intents) ---
            appointment_outcome = None
            if intent.get("intent_type") in ("new_appointment", "reschedule", None) or not uploaded_files:
                appointment_outcome = self.appointment_agent.run(
                    db, patient_id, department_id, request_text,
                    intent_type=intent.get("intent_type") or "new_appointment",
                )
                workflow_tools.update_workflow_state(
                    db, run.id, "appointment_processed", {"appointment": appointment_outcome}, self.name
                )
                result["appointment"] = appointment_outcome

            # --- Document Agent ---
            if uploaded_files:
                stored = self.document_agent.ingest_files(db, patient_id, uploaded_files)
                document_outcome = self.document_agent.run(db, patient_id, department_id, stored)
                workflow_tools.update_workflow_state(
                    db, run.id, "documents_processed", {"documents": document_outcome}, self.name
                )
                result["documents"] = document_outcome

            # --- Follow-up Agent (only if an appointment was actually booked) ---
            if appointment_outcome and appointment_outcome.get("booked"):
                appt = db.get(Appointment, appointment_outcome["appointment_id"])
                followup_outcome = self.followup_agent.run(
                    db, patient_id, appt.id, appt.slot.start_time.isoformat()
                )
                workflow_tools.update_workflow_state(
                    db, run.id, "followup_scheduled", {"followup": followup_outcome}, self.name
                )
                result["followup"] = followup_outcome

            workflow_tools.set_workflow_status(db, run.id, WorkflowStatus.completed, self.name)
            result["status"] = "completed"
            return result

        except Exception as exc:  # noqa: BLE001 - never let an unhandled agent error corrupt workflow state
            logger.exception("Coordinator run failed for workflow %s", run.id)
            workflow_tools.update_workflow_state(db, run.id, "error", {"error": str(exc)}, self.name)
            workflow_tools.set_workflow_status(db, run.id, WorkflowStatus.failed, self.name)
            result["status"] = "failed"
            result["error"] = str(exc)
            return result
