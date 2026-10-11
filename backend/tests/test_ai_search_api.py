from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.ai_search import AISearchService
from app.main import app
from app.schemas import RepurposeResponse


PAYLOAD = {
    "items": [
        {"name": "arduino_uno", "kind": "component"},
        {"name": "soil_moisture_sensor", "kind": "component"},
        {"name": "led", "kind": "component"},
        {"name": "resistor", "kind": "component"},
        {"name": "buzzer", "kind": "component"},
    ],
    "options": {"min_score": 0, "max_results": 10},
}


class SuccessfulAI:
    async def rank_recommendations(self, *, recommendations, **_: object) -> list[str]:
        # It returns only IDs already supplied by the deterministic matcher.
        return [row["project_id"] for row in recommendations]


class FailingAI:
    def __init__(self, status_code: int, detail: str) -> None:
        self.error = HTTPException(status_code=status_code, detail=detail)

    async def rank_recommendations(self, **_: object) -> list[str]:
        raise self.error


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _use_service(service: object) -> None:
    app.state.ai_search_service = service


def test_ai_search_accepts_same_payload_and_returns_same_schema(client: TestClient):
    _use_service(SuccessfulAI())
    classic = client.post("/api/v1/repurpose", json=PAYLOAD)
    ai = client.post("/api/v1/ai_search", json=PAYLOAD)

    assert classic.status_code == ai.status_code == 200
    RepurposeResponse.model_validate(ai.json())
    assert set(classic.json()) == set(ai.json())
    assert set(classic.json()["meta"]) == set(ai.json()["meta"])
    assert set(classic.json()["recommendations"][0]) == set(ai.json()["recommendations"][0])


def test_ai_search_is_documented_with_existing_request_and_response_models(client: TestClient):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    repurpose = paths["/api/v1/repurpose"]["post"]
    ai_search = paths["/api/v1/ai_search"]["post"]
    assert repurpose["requestBody"]["content"]["application/json"]["schema"] == ai_search["requestBody"]["content"]["application/json"]["schema"]
    assert repurpose["responses"]["200"]["content"]["application/json"]["schema"] == ai_search["responses"]["200"]["content"]["application/json"]["schema"]


def test_ai_search_requires_ai_model(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    _use_service(AISearchService())
    response = client.post("/api/v1/ai_search", json=PAYLOAD)
    assert response.status_code == 503
    assert "AI_MODEL" in response.json()["detail"]


def test_ai_search_requires_tavily_key(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    _use_service(AISearchService())
    response = client.post("/api/v1/ai_search", json=PAYLOAD)
    assert response.status_code == 503
    assert "TAVILY_API_KEY" in response.json()["detail"]


def test_ai_search_surfaces_tavily_failure_without_secret(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.setenv("TAVILY_API_KEY", "secret-value")
    service = AISearchService()
    service._search_tavily = MagicMock(side_effect=RuntimeError("network failure"))
    _use_service(service)
    response = client.post("/api/v1/ai_search", json=PAYLOAD)
    assert response.status_code == 502
    assert "secret-value" not in response.text


def test_ai_search_surfaces_ollama_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    _use_service(FailingAI(503, "Local AI model is unavailable. Check the Ollama server and configured model."))
    response = client.post("/api/v1/ai_search", json=PAYLOAD)
    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_ai_search_surfaces_invalid_ollama_output(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    service = AISearchService()

    async def empty_research(*_: object, **__: object) -> list[dict[str, str]]:
        return []

    service._research = empty_research
    service._invoke_ollama = MagicMock(return_value={"not_decisions": []})
    _use_service(service)
    response = client.post("/api/v1/ai_search", json=PAYLOAD)
    assert response.status_code == 502
    assert "invalid recommendation" in response.json()["detail"]


def test_ai_search_handles_no_viable_projects(client: TestClient):
    _use_service(SuccessfulAI())
    response = client.post("/api/v1/ai_search", json={"items": [{"name": "flux capacitor"}]})
    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert response.json()["unrecognized"][0]["input"] == "flux capacitor"


def test_ai_search_successful_response(client: TestClient):
    _use_service(SuccessfulAI())
    response = client.post("/api/v1/ai_search", json=PAYLOAD)
    assert response.status_code == 200
    assert response.json()["meta"]["mode"] == "match"
    assert response.json()["recommendations"]
