"""Wire-level response contracts, including failures outside domain handlers."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from test_marketplace import BASE, action, browser, data_request, launch, user

from market import service
from market.config import settings
from market.db import session
from market.identity import IDENTITY_JSON_OPERATIONS, IDENTITY_REDIRECT_OPERATIONS
from market.main import app
from market.responses import CONTRACTS, IDEMPOTENT_OPERATIONS, ErrorResponse


def assert_envelope(response, label, status=200):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {label, "status_code"}, body
    assert body["status_code"] == status and isinstance(body[label], dict)
    if label == "error":
        ErrorResponse.model_validate(body)
    return body[label]


def test_resource_labels_for_authenticated_reads(client):
    buyer, seller = user(client, "buyer"), user(client, "forma")
    for path, headers, label, items in [
        ("/health", {}, "health", False),
        ("/auth/me", buyer, "identity", False),
        ("/workspaces", buyer, "workspaces", True),
        ("/credentials", seller, "credentials", True),
        ("/categories", buyer, "categories", True),
        ("/products?limit=2&offset=1", buyer, "products", True),
        ("/products/prod-forma", buyer, "product", False),
        ("/products/prod-forma/history", seller, "product_history", True),
        ("/products/prod-forma/images", buyer, "images", True),
        ("/rules", buyer, "rules", False),
        ("/rules?product_id=prod-forma", seller, "rules", False),
        ("/rules/history", buyer, "rule_history", True),
        ("/requests", buyer, "requests", True),
        ("/negotiations", seller, "negotiations", True),
        ("/orders", buyer, "orders", True),
        ("/activity", buyer, "activity", True),
        ("/agent/instructions", buyer, "instructions", False),
        ("/agent/inbox", seller, "inbox", True),
    ]:
        value = assert_envelope(client.get(BASE + path, headers=headers), label)
        if items:
            assert isinstance(value["items"], list)
        if label == "products":
            assert (value["total"], value["limit"], value["offset"]) == (3, 2, 1)
    rid, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    for path, label in [
        (f"/requests/{rid}", "request_detail"),
        (f"/requests/{rid}/matches", "matches"),
        (f"/requests/{rid}/compare", "comparison"),
        (f"/negotiations/{room['id']}", "negotiation"),
        (f"/negotiations/{room['id']}/context", "context"),
        (f"/approvals/{room['id']}", "negotiation"),
    ]:
        assert_envelope(client.get(BASE + path, headers=buyer), label)
    own = assert_envelope(client.get(BASE + f"/negotiations/{room['id']}/context", headers=seller), "context")
    assert (
        not {"description", "max_total", "max_deposit_percent", "preference", "matches"}
        & own["request"].keys()
    )
    assert own["private_rules"]["min_unit_price"] == 8300
    assert "max_total" not in own["private_rules"]
    assert_envelope(action(client, seller, room["id"], "decline"), "action")


def test_issued_credentials_and_created_resources_keep_actual_http_status(client):
    buyer = user(client, "buyer")
    credential = assert_envelope(
        client.post(
            BASE + "/credentials",
            headers=buyer,
            json={"name": "Contract buyer", "scopes": ["catalogue:read"]},
        ),
        "credential",
        201,
    )
    assert isinstance(credential["token"], str)
    assert_envelope(client.delete(BASE + "/credentials/" + credential["id"], headers=buyer), "credential")
    result = client.post(BASE + "/requests", headers=buyer, json=data_request())
    request = assert_envelope(result, "request", 201)
    assert client.post(BASE + "/requests", headers=buyer, json=data_request()).json() == result.json()
    assert_envelope(client.post(BASE + "/requests/" + request["id"] + "/cancel", headers=buyer), "request")
    b = browser("buyer")
    assert_envelope(b.post(BASE + "/auth/logout"), "session")
    assert_envelope(b.get(BASE + "/auth/me"), "error", 401)


def test_all_error_paths_are_enveloped_and_sensitive_inputs_are_omitted(client, monkeypatch):
    buyer = user(client, "buyer")
    for method, path, kwargs, status, code in [
        ("GET", "/auth/me", {}, 401, "unauthenticated"),
        ("GET", "/images/not-visible/content", {"headers": buyer}, 404, "image_not_found"),
        ("GET", "/not-a-route", {}, 404, "not_found"),
        ("GET", "/health/", {}, 404, "not_found"),
        ("PATCH", "/health", {}, 405, "method_not_allowed"),
        (
            "PUT",
            "/products/prod-forma/stock",
            {"headers": buyer, "json": {"stock": 1, "expected_version": 1}},
            403,
            "permission_denied",
        ),
        (
            "POST",
            "/auth/token",
            {"json": {"email": "a", "password": "private-password"}},
            422,
            "validation_error",
        ),
        (
            "POST",
            "/auth/token",
            {"content": "{}", "headers": {"Content-Length": str(settings.max_image_bytes + 65537)}},
            413,
            "body_too_large",
        ),
    ]:
        response = client.request(method, BASE + path, **kwargs)
        error = assert_envelope(response, "error", status)
        assert error["code"] == code
        assert "private-password" not in response.text
        if status == 405:
            assert "GET" in response.headers["allow"]

    # The database conflict handler must use the same transport contract.
    def conflict(*args, **kwargs):
        raise IntegrityError("SECRET SQL", {}, ValueError("private constraint"))

    monkeypatch.setattr(service, "product_view", conflict)
    response = client.get(BASE + "/products/prod-forma", headers=buyer)
    assert assert_envelope(response, "error", 409)["code"] == "conflict"
    assert "SECRET" not in response.text and "constraint" not in response.text


def test_unexpected_and_response_validation_errors_are_sanitized(client, monkeypatch):
    buyer = user(client, "buyer")
    monkeypatch.setattr(service, "product_view", lambda *args, **kwargs: {"private": "secret-value"})
    with TestClient(app, raise_server_exceptions=True) as safe_client:
        response = safe_client.get(BASE + "/products/prod-forma", headers=buyer)
    assert assert_envelope(response, "error", 500)["code"] == "internal_error"
    assert "secret-value" not in response.text
    assert response.headers["cache-control"] == "no-store"

    for exception in (
        RuntimeError("postgres://secret-password@private-host"),
        HTTPException(500, "postgres://secret-password@private-host"),
    ):

        def broken_database():
            raise exception

        monkeypatch.setitem(app.dependency_overrides, session, broken_database)
        with TestClient(app, raise_server_exceptions=True) as safe_client:
            response = safe_client.get(BASE + "/health")
        assert assert_envelope(response, "error", 500)["code"] == "internal_error"
        assert "private-host" not in response.text and "secret-password" not in response.text


def test_cors_preflights_are_enveloped_without_losing_protocol_headers():
    with TestClient(app) as client:
        allowed = client.options(
            BASE + "/products",
            headers={
                "Origin": "http://localhost:3100",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Workspace-ID",
            },
        )
        assert assert_envelope(allowed, "cors") == {"allowed": True}
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:3100"
        assert allowed.headers["access-control-allow-credentials"] == "true"
        for origin, header in (
            ("https://unrelated.example", "X-Workspace-ID"),
            ("http://localhost:3100", "X-Forbidden"),
        ):
            rejected = client.options(
                BASE + "/products",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": header,
                },
            )
            assert assert_envelope(rejected, "error", 400)["code"] == "cors_rejected"
        unsupported = client.options(BASE + "/products")
        assert assert_envelope(unsupported, "error", 405)["code"] == "method_not_allowed"


def test_rate_limit_response_contract(client):
    # Login is the only unauthenticated write; failed passwords still consume its window.
    for _ in range(20):
        response = client.post(
            BASE + "/auth/token", json={"email": "missing@demo.local", "password": "wrong"}
        )
        assert_envelope(response, "error", 401)
    response = client.post(BASE + "/auth/token", json={"email": "missing@demo.local", "password": "wrong"})
    assert assert_envelope(response, "error", 429)["code"] == "rate_limited"


def test_openapi_matches_every_json_endpoint_and_binary_exception():
    schema = app.openapi()
    snapshot = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
    assert json.loads(snapshot.read_text()) == schema, "Run uv run python scripts/export_openapi.py"
    components = schema["components"]["schemas"]
    names = set()
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            names.add(operation["operationId"])
            assert operation["description"] and operation["tags"]
            if operation["operationId"] in IDEMPOTENT_OPERATIONS:
                key = next(item for item in operation["parameters"] if item["name"] == "Idempotency-Key")
                assert key["required"] is True
                assert key["schema"] == {"type": "string", "minLength": 8, "maxLength": 128}
            if operation["operationId"] in ("approval_accept", "approval_reject", "negotiations_guidance"):
                for header in ("Origin", "X-CSRF-Token"):
                    assert next(item for item in operation["parameters"] if item["name"] == header)[
                        "required"
                    ]
            for status, definition in operation["responses"].items():
                if int(status) >= 400:
                    assert definition["content"]["application/json"]["schema"] == {
                        "$ref": "#/components/schemas/ErrorResponse"
                    }
                    continue
                if operation["operationId"] == "image_content":
                    assert set(definition["content"]) == {"image/jpeg"}
                    assert "x-object-label" not in operation
                    continue
                if operation["operationId"] in IDENTITY_REDIRECT_OPERATIONS:
                    assert status == "303"
                    assert operation["x-response-format"] == "redirect"
                    continue
                ref = definition["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
                envelope = components[ref]
                label = operation["x-object-label"]
                assert set(envelope["required"]) == {label, "status_code"}
                assert set(envelope["properties"]) == {label, "status_code"}
                assert envelope["properties"]["status_code"]["const"] == int(status)
                payload = envelope["properties"][label]["$ref"].split("/")[-1]
                assert components[payload]["type"] == "object"
                assert envelope["additionalProperties"] is False
    assert (
        names == set(CONTRACTS) | {"image_content"} | IDENTITY_JSON_OPERATIONS | IDENTITY_REDIRECT_OPERATIONS
    )
    assert schema["paths"][BASE + "/auth/token"]["post"]["security"] == []
    assert schema["paths"][BASE + "/approvals/{room_id}/approve"]["post"]["security"] == [
        {"BrowserSession": []}
    ]
    with TestClient(app) as client:
        exported = client.get("/openapi.json").json()
        assert exported["openapi"] == schema["openapi"] and "status_code" not in exported
