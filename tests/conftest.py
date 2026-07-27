"""Shared pytest fixtures — every test runs against a fresh in-memory SQLite database,
never the real agentcare.db file."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before any `app.*` module is imported, since app.config reads the environment
# at import time. Keeps tests from ever touching the real agentcare.db file.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.gettempdir()}/agentcare_pytest.db")
os.environ.setdefault("GROQ_API_KEY", "test-key-not-a-real-key")
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.database import Base, get_db
from app.models import (
    AppointmentSlot,
    Department,
    Doctor,
    PatientProfile,
    SlotStatus,
    User,
    UserRole,
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def db_session(engine):
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def seeded(db_session):
    """Minimal deterministic seed data: one department, one doctor, a few future slots,
    one patient, one staff user."""
    from datetime import datetime, timedelta, timezone

    dept = Department(name="Cardiology", description="Heart care", required_documents="ecg,blood_report")
    db_session.add(dept)
    db_session.flush()

    doctor = Doctor(name="Dr. Test Cardio", department_id=dept.id, active=True)
    db_session.add(doctor)
    db_session.flush()

    base = datetime.now(timezone.utc) + timedelta(days=1)
    slots = []
    for i in range(3):
        slot = AppointmentSlot(
            doctor_id=doctor.id,
            start_time=base + timedelta(days=i),
            end_time=base + timedelta(days=i, minutes=30),
            status=SlotStatus.available,
        )
        db_session.add(slot)
        slots.append(slot)
    db_session.flush()

    patient_user = User(
        name="Test Patient", email="patient@test.local", password_hash=hash_password("pw123456"), role=UserRole.patient
    )
    db_session.add(patient_user)
    db_session.flush()
    patient_profile = PatientProfile(user_id=patient_user.id)
    db_session.add(patient_profile)

    staff_user = User(
        name="Test Staff", email="staff@test.local", password_hash=hash_password("pw123456"), role=UserRole.staff
    )
    db_session.add(staff_user)

    db_session.commit()

    return {
        "department": dept,
        "doctor": doctor,
        "slots": slots,
        "patient_user": patient_user,
        "patient_profile": patient_profile,
        "staff_user": staff_user,
    }


@pytest.fixture()
def client(engine, seeded):
    """FastAPI TestClient wired to the same in-memory engine as `seeded`."""
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.main import app

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
