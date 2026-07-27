"""Proves role-based access control is enforced in backend routes, not just hidden UI."""


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def test_patient_can_reach_patient_routes_but_not_staff(client):
    resp = login(client, "patient@test.local", "pw123456")
    assert resp.status_code == 303

    dash = client.get("/patient")
    assert dash.status_code == 200

    staff_dash = client.get("/staff")
    assert staff_dash.status_code == 403


def test_staff_can_reach_staff_routes_but_not_patient(client):
    resp = login(client, "staff@test.local", "pw123456")
    assert resp.status_code == 303

    dash = client.get("/staff")
    assert dash.status_code == 200

    patient_dash = client.get("/patient")
    assert patient_dash.status_code == 403

    audit = client.get("/staff/audit")
    assert audit.status_code == 200


def test_unauthenticated_requests_are_redirected_to_login(client):
    resp = client.get("/patient", follow_redirects=False)
    assert resp.status_code in (303, 401)


def test_invalid_login_rejected(client):
    resp = client.post("/login", data={"email": "patient@test.local", "password": "wrong-password"})
    assert resp.status_code == 401


def test_duplicate_registration_rejected(client):
    payload = {"name": "Dup", "email": "patient@test.local", "password": "pw123456"}
    resp = client.post("/register", data=payload)
    assert resp.status_code == 400
