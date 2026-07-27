# AgentCare — Agentic AI for Patient Administration and Care Coordination

AgentCare is a hospital **administrative** assistant. A patient describes what they need in
plain language — "I need a cardiology follow-up next week and want to attach my old ECG" —
and a coordinated team of AI agents identifies the patient, routes the request, books an
appointment, files the documents, and schedules reminders, persisting every step to a real
database with a full audit trail.

**AgentCare never diagnoses, prescribes, adjusts dosages, or claims to replace a clinician.**
It handles scheduling and administration only; anything clinical, uncertain, or emergency is
escalated to a human staff member for review.

## Quick start

```bash
git clone <this-repo>
cd PatientPilot
uv venv myenv
myenv\Scripts\activate        # Windows: venv\Scripts\activate
uv pip install -r requirements.txt

cp .env.example .env
# edit .env and set GROQ_API_KEY (free key at https://console.groq.com/keys)

python -m app.seed              # optional — the app also auto-seeds on first boot
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. Demo accounts (created by the seed script):

| Role    | Email                     | Password         |
|---------|---------------------------|-------------------|
| Patient | patient@agentcare.demo    | PatientPass123!   |
| Staff   | staff@agentcare.demo      | StaffPass123!     |

Run the test suite:

```bash
pytest tests/ -v
```

All 19 tests run against an in-memory SQLite database and require no API key — the LLM
reasoning steps are exercised with deterministic stand-ins so the suite is reproducible in CI.

## Architecture

```
Patient / Staff (browser, Jinja2 templates)
        │
        ▼
FastAPI routes  ── backend-enforced RBAC (app/auth.py) ──▶ 403 if wrong role
        │
        ▼
Coordinator Agent (app/agents/coordinator.py)
  ├─ extracts structured intent (own LLM prompt)
  ├─ creates a WorkflowRun row, persists state after every hop
  ├─▶ Safety & Escalation Agent  (runs first, can halt the pipeline)
  ├─▶ Department Routing Agent
  ├─▶ Appointment Agent
  ├─▶ Document Agent
  └─▶ Follow-up Agent
        │
        ▼
Tools (app/tools/*.py) — real functions that read/write SQLite via SQLAlchemy
        │
        ▼
SQLite database (agentcare.db) — persists across restarts
        │
        ▼
Every tool call writes an AuditEvent row (app/tools/audit_tools.py)
```

The final confirmation shown to the patient is built by **re-reading the persisted database
rows**, never by trusting LLM-generated text as fact.

## Agents (6 genuinely distinct roles)

Each agent below has its own system prompt and its own restricted tool set — none of them
share a prompt, and none of them can reach into another agent's tools.

| Agent | File | Responsibility | Tools it can call |
|---|---|---|---|
| **Coordinator** | `app/agents/coordinator.py` | Extracts intent, creates/tracks the WorkflowRun, delegates to every other agent in order, merges results, marks completed/failed | `find_or_create_patient` (via patient_tools, at registration) |
| **Safety & Escalation** | `app/agents/safety_agent.py` | Screens every request for emergencies, diagnosis requests, and prescription/dosage requests; blocks the pipeline and escalates | `escalate` only — no clinical or booking tools |
| **Department Routing** | `app/agents/routing_agent.py` | Classifies the request and maps it to a real `Department` row; escalates if unclear | `list_departments`, `classify_department`, `escalate_routing` |
| **Appointment** | `app/agents/appointment_agent.py` | Finds real available slots, checks conflicts, books | `get_available_slots`, `book_appointment` |
| **Document** | `app/agents/document_agent.py` | Stores/classifies uploaded files (checksum + keyword heuristics), flags duplicates/missing docs | `check_missing_documents` (storage itself is deterministic, done before the reasoning step) |
| **Follow-up** | `app/agents/followup_agent.py` | Creates appointment reminders and post-visit follow-up tasks, dispatches notifications | `create_reminder`, `dispatch_notification` |

### Safety boundary (defense in depth)

The Safety Agent runs a **deterministic keyword screen first** (in code, not the LLM) for
emergency/diagnosis/prescription language. If nothing hits, a second LLM pass reviews softer,
ambiguous cases — but that pass only has access to a single `escalate` tool. Neither layer can
diagnose, prescribe, or suggest treatment; the only action either can take is to create a
persisted `Escalation` row for a human to review. See `tests/test_safety_agent.py`.

## Tools (8 implemented, all touch real data)

`patient_tools`, `department_tools`, `appointment_tools`, `document_tools`,
`reminder_tools`, `escalation_tools`, `audit_tools`, `workflow_tools` — every function reads
from or writes to the SQLite database (see `app/tools/`). None return a fixed value regardless
of input; see `tests/test_tools.py` for direct proof (conflict detection, duplicate document
checksums, missing-document diffing, etc.).

## Data model

`User → PatientProfile → {Appointment, PatientDocument, WorkflowRun, Reminder}`,
`Department → Doctor → AppointmentSlot → Appointment`, `WorkflowRun → Escalation`,
`AuditEvent` (global action trail). Full schema in `app/models.py`. `WorkflowRun.state` is a
JSON blob that agents read from and write to as they hand off work to one another — this is
what makes the workflow durable across restarts instead of living only in memory.

## Role-based access control

`app/auth.py` defines `require_patient` / `require_staff` FastAPI dependencies used on every
protected route. A patient's session can only ever act on their own `PatientProfile` records
(`assert_owns_patient`); staff can view all patients and resolve escalations. This is enforced
in the route handlers themselves — see `tests/test_rbac.py`, which asserts a patient hitting
`/staff` gets a `403`, not just a hidden button.

## Human escalation / approval workflow

Any request the Safety Agent or Routing Agent can't confidently handle creates an `Escalation`
row and sets the `WorkflowRun` status to `escalated`. Staff review pending escalations at
`/staff/escalations` and approve or reject them (`app/tools/escalation_tools.py`), which is
itself an audited action.

## Error handling & recovery

`app/llm.py` wraps every Groq call in exponential-backoff retry (`chat_completion_with_retry`).
Tool execution failures inside the agent loop are caught per-call and surfaced back to the
model as a tool error rather than crashing the request. The Coordinator wraps the whole
pipeline in a try/except that marks the `WorkflowRun` `failed` and logs the error instead of
leaving inconsistent state.

## Environment configuration

All configuration is environment-based (`app/config.py`, `pydantic-settings`) — see
`.env.example`. No secrets are committed; `.gitignore` excludes `.env` and the SQLite file.

## Synthetic data

`app/seed.py` creates non-real departments, doctors, future appointment slots, and two demo
accounts. No real patient data is included anywhere in this repository.

## Project layout

```
app/
  main.py            FastAPI app + startup (init DB, auto-seed)
  config.py           environment-based settings
  database.py          SQLAlchemy engine/session
  models.py             ORM models
  auth.py                login/session/RBAC
  llm.py                  Groq client + retry + generic tool-calling loop
  seed.py                  synthetic data
  agents/                 the 6 agents described above
  tools/                    the 8 tools described above
  routers/                   patient.py, staff.py, auth_router.py
  templates/                   Jinja2 UI (patient/ and staff/)
  static/                       style.css
tests/                    pytest suite (tools, RBAC, safety agent, full e2e workflow)
.github/workflows/          agentcare-checks.yml (hackathon CI)
```

## What's a stub vs. what's real

Everything under `app/tools/` performs real SQL reads/writes. The one deliberately-simple
piece is notification delivery (`reminder_tools.dispatch_notification`): it persists a real
state transition (`Reminder.status -> sent`) and writes an audit event, but doesn't call an
external SMS/email provider — swapping in a real provider is a one-function change and doesn't
touch any agent code.

## Disclosure

Built for the AgentCare Build Challenge 2026. Application source, prompts, and orchestration
logic are original to this submission. Third-party libraries used are declared in
`requirements.txt` (FastAPI, SQLAlchemy, Jinja2, Groq SDK, bcrypt, pytest, etc.).
