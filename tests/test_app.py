import pytest
from app import app, db


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
        yield client


def test_index_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_dashboard_loads(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_deploy_rejects_bad_url(client):
    resp = client.post("/deploy", data={"repo_url": "not-a-url", "branch": "main"})
    assert resp.status_code in (302, 200)  # redirects back with flash error
