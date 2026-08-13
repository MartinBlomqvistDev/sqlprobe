"""The HTTP surface, including the pseudonymisation boundary."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from nl2sql.api import app
from nl2sql.config import Settings, get_settings


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(app)


def test_health_reports_the_database(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["services"]["database"] == "ok"


def test_resolve_returns_a_person(client: TestClient, settings: Settings) -> None:
    response = client.get("/api/resolve/cus_00001")
    assert response.status_code == 200
    payload = response.json()
    assert payload["customer_ref"] == "cus_00001"
    assert payload["full_name"]
    assert payload["contact_email"].endswith("@example.invalid")


def test_resolve_of_an_unknown_reference_returns_nulls(client: TestClient) -> None:
    payload = client.get("/api/resolve/cus_99999").json()
    assert payload["full_name"] is None
    assert payload["contact_email"] is None


def test_resolve_rejects_a_malformed_reference(client: TestClient) -> None:
    payload = client.get("/api/resolve/' OR 1=1--").json()
    assert payload["full_name"] is None


def test_the_analytics_database_holds_no_identities(settings: Settings) -> None:
    """The boundary is only real if the analytics tables genuinely lack names."""
    import asyncio

    from nl2sql.db import run_select

    result = asyncio.run(
        run_select("SELECT name FROM pragma_table_info('customers')")
    )
    columns = {row["name"] for row in result["rows"]}
    for forbidden in ("full_name", "contact_email", "name", "email", "phone"):
        assert forbidden not in columns, f"{forbidden} must not be in the analytics DB"


def test_chat_rejects_an_empty_question(client: TestClient) -> None:
    assert client.post("/api/chat", json={"question": ""}).status_code == 422


def test_api_key_is_enforced_when_configured(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", "s3cret")
    get_settings.cache_clear()
    try:
        with TestClient(app) as guarded:
            assert guarded.get("/api/resolve/cus_00001").status_code == 401
            allowed = guarded.get(
                "/api/resolve/cus_00001", headers={"X-API-Key": "s3cret"}
            )
            assert allowed.status_code == 200
    finally:
        get_settings.cache_clear()


def test_chat_streams_an_error_when_no_provider_key_is_set(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no key configured the stream must still terminate cleanly."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(app) as unkeyed:
            with unkeyed.stream(
                "POST", "/api/chat", json={"question": "How many customers?"}
            ) as response:
                assert response.status_code == 200
                events = [
                    json.loads(line[6:])
                    for line in response.iter_lines()
                    if line.startswith("data: ")
                ]
        kinds = [event["type"] for event in events]
        assert "error" in kinds
        assert kinds[-1] == "done", "every stream must end with done"
    finally:
        get_settings.cache_clear()
