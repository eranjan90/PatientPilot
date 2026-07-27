# AgentCare — Build Plan

Stack: Python, FastAPI + Jinja2, SQLite (SQLAlchemy), Groq LLM (OpenAI-compatible tool-calling), custom orchestrator (no framework).

## 1. Architecture

```
Patient/Staff (browser)
   -> FastAPI routes (role-checked)
       -> Coordinator Agent
            -> Safety Agent          (runs first, can halt pipeline)
            -> Department Routing Agent
            -> Appointment Agent
            -> Document Agent
            -> Follow-up Agent
       -> each agent calls Tools (real functions)
            -> Tools read/write SQLite via SQLAlchemy
            -> every tool call writes an AuditEvent
   -> WorkflowRun row persists current_step + state (JSON) after every agent hop
   -> final confirmation is built by reading back the persisted DB rows, never invented by the LLM
```

Each agent = its own system prompt + its own restricted tool subset + a small LLM tool-calling loop (call model -> if it requests a tool, run the real Python function -> feed result back -> repeat until it returns a structured final answer). This satisfies "no hardcoded final responses" and "genuinely distinct agents."

## 2. Folder structure

```
agentcare/
  app/
    main.py            FastAPI app, mounts routers
    config.py          pydantic-settings, reads .env
    database.py         SQLAlchemy engine/session
    models.py           ORM models (schema below)
    auth.py              login, session cookie, role-check dependencies
    llm.py                Groq client wrapper + generic tool-calling loop
    routers/
      patient.py         patient-facing pages/actions
      staff.py            staff dashboard, escalations, approvals, audit view
    agents/
      coordinator.py
      safety_agent.py
      routing_agent.py
      appointment_agent.py
      document_agent.py
      followup_agent.py
    tools/
      patient_tools.py     lookup/create patient
      department_tools.py  list/classify departments
      appointment_tools.py availability, book, reschedule, cancel, conflict check
      document_tools.py    classify, store metadata, checksum, duplicate/missing check
      reminder_tools.py    create reminder/follow-up, notification stub (persisted, not fake)
      escalation_tools.py  create/approve/reject escalation
      audit_tools.py       write AuditEvent (called by every other tool)
    templates/
      patient/  (request form, status page, documents, reminders)
      staff/    (queue, escalations, doctors/slots admin, audit log)
    static/
    seed.py               synthetic patients/departments/doctors/slots
  tests/
    test_tools.py
    test_agents.py
    test_rbac.py
    test_workflow_e2e.py
  .github/workflows/agentcare-checks.yml
  requirements.txt
  .env.example
  .gitignore
  README.md
```

## 3. Database schema (SQLite via SQLAlchemy)

Matches the spec's suggested model: User, PatientProfile, Department, Doctor, AppointmentSlot, Appointment, PatientDocument, WorkflowRun, Reminder, Escalation, AuditEvent. `WorkflowRun.state` stored as JSON text — the shared memory agents pass between each other and that survives restarts.

## 4. Agents (all 6, each with distinct prompt + tools)

1. **Coordinator** — parses the free-text request, creates/loads the WorkflowRun, decides which agents to invoke and in what order, merges outputs, marks the run completed/failed.
2. **Safety & Escalation Agent** — runs first on every request; screens for emergency/diagnosis/prescription language; if triggered, creates an `Escalation` row, halts the pipeline, requires staff approval before anything else proceeds.
3. **Department Routing Agent** — classifies intent, maps to a real `Department` row, escalates if unclear/unsupported.
4. **Appointment Agent** — queries real `AppointmentSlot` rows, checks conflicts, books/reschedules/cancels `Appointment` rows.
5. **Document Agent** — handles uploaded files, classifies type, computes checksum, stores metadata, flags duplicates/missing required docs for the department.
6. **Follow-up Agent** — creates `Reminder` rows and a follow-up task, checks for incomplete workflows.

## 5. Tools (>=3 required, building 8)

patient lookup/create, department lookup, slot availability, appointment book/reschedule/cancel, document classify+store, duplicate/missing-document check, reminder/notification creation, escalation create/approve, audit log write. All perform real DB reads/writes — no fixed-response stubs.

## 6. Safety boundary (hard rule, enforced in code, not just prompts)

- Safety Agent's tool set never includes anything that writes a diagnosis/prescription.
- A keyword+LLM classifier layer blocks requests like "what medicine should I take" / "diagnose my chest pain" from reaching the Appointment/Document agents — routes straight to Escalation instead.
- System prompts for every agent explicitly forbid clinical judgments; this is tested in `test_agents.py` with adversarial prompts.

## 7. RBAC (backend-enforced)

`User.role` = `patient` or `staff`. FastAPI dependency checks session role + resource ownership (a patient can only see their own `WorkflowRun`/`Appointment`/`Documents`; staff can see all + approve escalations). Enforced in route handlers, not just hidden in the UI.

## 8. Build phases

1. **Scaffold** — repo, requirements.txt, .env.example, .gitignore, DB models, seed script, CI workflow file.
2. **Auth + RBAC** — register/login, patient vs staff sessions, protected routes.
3. **Tools layer** — plain functions with real DB logic, unit-tested in isolation first.
4. **LLM + agent layer** — Groq tool-calling loop, then each of the 6 agents with its own prompt/tool subset.
5. **Coordinator orchestration** — wires agents together, persists WorkflowRun state at every step, audit logs every action.
6. **UI wiring** — patient request form -> live status page; staff queue, escalation approval, audit log view.
7. **Document upload pipeline** — file upload -> Document Agent -> classification/duplicate check.
8. **Tests + README + architecture doc + demo/seed data + polish.**
9. **Push, enable the hackathon CI checks, verify they pass.**

## 9. Deliverable checklist (from the rules)

- [ ] Public GitHub repo, real source
- [ ] Python backend (FastAPI)
- [ ] LLM integration (Groq) + multi-step tool-using agents
- [ ] 3+ distinct agents (building 6)
- [ ] 3+ real tools (building 8)
- [ ] Persistent SQL DB (SQLite)
- [ ] Persistent workflow/agent state (WorkflowRun)
- [ ] Working UI for patient + staff (FastAPI + Jinja2)
- [ ] Backend-enforced RBAC
- [ ] Human escalation/approval workflow
- [ ] Audit logging
- [ ] Error handling + retry
- [ ] Env-based config (.env)
- [ ] Synthetic seed data
- [ ] README with setup + architecture
- [ ] No hardcoded final responses
- [ ] .env.example, .gitignore, tests
- [ ] .github/workflows/agentcare-checks.yml + SUBMISSION_TOKEN secret
