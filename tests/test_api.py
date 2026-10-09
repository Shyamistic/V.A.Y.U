from fastapi.testclient import TestClient

from monsoon_grid.api import app


def test_api_health_openapi_and_no_live_control_route():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/validate" in schema["paths"] and "/demo" in schema["paths"]
    assert not any("live" in path.lower() or "scada" in path.lower() for path in schema["paths"])


def test_idempotency_header_is_required():
    client = TestClient(app)
    response = client.post("/demo", json={"idempotency_key": "api-test", "deadline_seconds": 0.1})
    assert response.status_code == 422
