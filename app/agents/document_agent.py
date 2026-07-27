"""Document Agent.

Distinct responsibility: coordinates document intake. Files are classified and stored
deterministically (checksum + keyword classification is not something that benefits from an
LLM guess), then this agent reasons over what's on file to flag missing/duplicate documents
for the patient's department using its own tool set.
"""
from app.llm import ToolRegistry, run_tool_loop
from app.tools import document_tools

MISSING_DOCS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_missing_documents",
        "description": "Check which required documents for a department are missing for this patient.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "integer"},
                "department_id": {"type": "integer"},
            },
            "required": ["patient_id", "department_id"],
        },
    },
}

SYSTEM_PROMPT = """You are the Document Agent for AgentCare. Documents the patient uploaded
have already been classified and stored. Your job is to call `check_missing_documents` for
this patient's department and summarize, in plain administrative language, what is on file,
what (if anything) is missing, and whether any uploads were duplicates. Never comment on the
clinical content of a document — only whether it exists and what type it was filed as. Finish
with a short JSON: {"missing": [...], "present": [...]}."""


class DocumentAgent:
    name = "document_agent"

    def ingest_files(self, db, patient_id: int, files: list[tuple[str, bytes]]) -> list[dict]:
        """Deterministic storage step — real checksum + classification logic, not an LLM guess."""
        results = []
        for filename, content in files:
            result = document_tools.store_document(db, patient_id, filename, content, actor_label=self.name)
            results.append(result)
        return results

    def run(self, db, patient_id: int, department_id: int, stored_results: list[dict]) -> dict:
        registry = ToolRegistry()
        registry.add(
            MISSING_DOCS_SCHEMA,
            lambda patient_id=patient_id, department_id=department_id: document_tools.check_missing_documents(
                db, patient_id, department_id
            ),
        )

        prompt = f"Patient ID: {patient_id}\nDepartment ID: {department_id}\nJust-uploaded documents: {stored_results}"
        outcome = run_tool_loop(SYSTEM_PROMPT, prompt, registry)

        for call in outcome["tool_calls"]:
            if call["tool"] == "check_missing_documents":
                return {**call["result"], "uploaded": stored_results, "summary": outcome["final_text"]}

        return {"missing": [], "present": [], "uploaded": stored_results, "summary": outcome["final_text"]}
