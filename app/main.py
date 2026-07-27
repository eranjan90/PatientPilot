"""AgentCare FastAPI application entrypoint."""
import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import auth_router, patient, staff

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="AgentCare", description="Agentic AI for Patient Administration and Care Coordination")


@app.on_event("startup")
def on_startup():
    init_db()
    # Auto-seed synthetic demo data on first boot so the app is immediately usable.
    from app.database import session_scope
    from app.models import Department

    with session_scope() as db:
        has_data = db.query(Department).first() is not None
    if not has_data:
        from app.seed import run_seed

        run_seed()


app.include_router(auth_router.router)
app.include_router(patient.router)
app.include_router(staff.router)

try:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
except RuntimeError:
    pass


@app.get("/")
def root():
    return RedirectResponse("/login")


@app.get("/health")
def health():
    return {"status": "ok"}
