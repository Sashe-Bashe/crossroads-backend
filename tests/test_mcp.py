"""MCP wire-level tool tests with isolated backend schemas and restricted credentials."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("fastmcp", reason="Install the MCP extra to run the MCP protocol tests.")

# Optional protocol dependencies are absent from the default API/worker image.
# ruff: noqa: E402
from fastmcp import Client
from fastmcp.exceptions import ToolError
from response_helpers import response_data

from market.auth import ROLE_SCOPES
from market.config import settings
from market.main import app
from mcp_server.client import Marketplace
from mcp_server.config import Configuration, ConfigurationError
from mcp_server.server import create_server

BASE = "/api/v1"
TOKEN = "am_" + "x" * 54


def config(token=TOKEN, workspace="ws-buyer", url="http://127.0.0.1:8100/api/v1"):
    return Configuration(api_url=url, workspace_id=workspace, token=token)


def agent(client, email="buyer", scopes=None):
    response = client.post(
        BASE + "/auth/token", json={"email": email + "@demo.local", "password": settings.demo_password}
    )
    admin = {"Authorization": "Bearer " + response_data(response)["token"]}
    identity = response_data(client.get(BASE + "/auth/me", headers=admin))
    role = identity["workspace"]["role"]
    issued = response_data(
        client.post(
            BASE + "/credentials",
            headers=admin,
            json={
                "name": "MCP isolated test",
                "scopes": scopes or sorted(ROLE_SCOPES[role]),
                "expires_days": 1,
            },
        )
    )
    return issued, admin


def server(credential):
    return create_server(
        Marketplace(
            config(credential["token"], credential["workspace_id"]),
            transport=httpx.ASGITransport(app=app),
        )
    )


def request_data():
    return {
        "title": "MCP buyer request",
        "quantity": 10,
        "material": "polypropylene",
        "stackable": True,
        "destination": "Sofia",
        "latest_delivery": (date.today() + timedelta(days=14)).isoformat(),
        "max_total": 120000,
        "max_deposit_percent": 40,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://market.example/api/v1",
        "https://user:secret@market.example/api/v1",
        "https://market.example/api/v1?token=secret",
        "https://market.example/api/v1#fragment",
        "http://127.0.0.1:8100/",
        "file:///api/v1",
    ],
)
def test_configuration_rejects_credential_leak_routes(url):
    with pytest.raises(ConfigurationError):
        config(url=url)
    assert TOKEN not in repr(config())


@pytest.mark.parametrize("token", ["", "admin-token", "am_short", TOKEN + "\n"])
def test_configuration_rejects_non_agent_token_shape(token):
    with pytest.raises(ConfigurationError) as exc:
        config(token=token)
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    "overrides", [{"kind": "cli"}, {"workspace": "another-workspace"}, {"can_approve": True}]
)
def test_startup_checks_identity_not_just_token_prefix(overrides):
    def handler(request):
        identity = {
            "credential": {"kind": overrides.get("kind", "agent")},
            "workspace": {"id": overrides.get("workspace", "ws-buyer"), "role": "buyer"},
            "can_approve": overrides.get("can_approve", False),
        }
        return httpx.Response(200, json={"identity": identity, "status_code": 200})

    async def run():
        api = Marketplace(config(), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(ConfigurationError):
                await api.validate_identity()
        finally:
            await api.close()

    asyncio.run(run())


def test_client_never_follows_redirect_or_returns_headers():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/collect"})

    async def run():
        api = Marketplace(config(), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(ToolError) as exc:
                await api.request("GET", "agent/inbox")
            assert TOKEN not in str(exc.value)
        finally:
            await api.close()

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0].url.host == "127.0.0.1"


def test_tools_and_real_readonly_scope(client):
    issued, admin = agent(
        client, scopes=["catalogue:read", "images:read", "negotiations:read", "orders:read"]
    )

    async def run():
        async with Client(server(issued)) as mcp:
            tools = await mcp.list_tools()
            names = {tool.name for tool in tools}
            assert len(names) == 13
            assert not names & {
                "approve_order",
                "approve",
                "update_rules",
                "create_credential",
                "http_request",
            }
            assert all(tool.annotations is not None for tool in tools)
            assert all("token" not in tool.inputSchema.get("properties", {}) for tool in tools)
            catalogue = await mcp.call_tool("search_catalogue", {"limit": 5})
            assert catalogue.structured_content["total"] >= 3
            assert len(catalogue.structured_content["items"]) <= 5
            instructions = await mcp.call_tool("get_instructions")
            assert instructions.structured_content["workspace_id"] == "ws-buyer"
            assert instructions.structured_content["role"] == "buyer"
            write = await mcp.call_tool(
                "create_request",
                {"request": request_data(), "idempotency_key": str(uuid4())},
                raise_on_error=False,
            )
            assert write.is_error
            assert "permission_denied" in write.content[0].text
            bad_id = await mcp.call_tool(
                "get_negotiation_context", {"room_id": "../credentials"}, raise_on_error=False
            )
            assert bad_id.is_error
            revoked = client.delete(BASE + "/credentials/" + issued["id"], headers=admin)
            assert revoked.status_code == 200
            read = await mcp.call_tool("get_inbox", raise_on_error=False)
            assert read.is_error
            assert "unauthenticated" in read.content[0].text

    asyncio.run(run())


def test_buyer_seller_isolation_and_action_guards(client):
    buyer, _ = agent(client)
    seller, _ = agent(client, "forma")
    outsider, _ = agent(client, "atelier")

    async def run():
        async with Client(server(buyer)) as b, Client(server(seller)) as s, Client(server(outsider)) as other:
            key = str(uuid4())
            made = await b.call_tool("create_request", {"request": request_data(), "idempotency_key": key})
            repeated = await b.call_tool(
                "create_request", {"request": request_data(), "idempotency_key": key}
            )
            request_id = made.structured_content["id"]
            assert repeated.structured_content["id"] == request_id
            denied = await s.call_tool(
                "create_request",
                {"request": request_data(), "idempotency_key": str(uuid4())},
                raise_on_error=False,
            )
            assert denied.is_error
            assert "permission_denied" in denied.content[0].text
            await b.call_tool("launch_request", {"request_id": request_id, "idempotency_key": str(uuid4())})
            inbox = (await s.call_tool("get_inbox")).structured_content["items"]
            assert inbox
            room_id = inbox[0]["room_id"]
            forbidden = await other.call_tool(
                "get_negotiation_context", {"room_id": room_id}, raise_on_error=False
            )
            assert forbidden.is_error
            seller_context = (
                await s.call_tool("get_negotiation_context", {"room_id": room_id})
            ).structured_content
            assert "max_total" not in str(seller_context.get("request", {}))
            claim = (await s.call_tool("claim_turn", {"room_id": room_id})).structured_content
            action = {
                "type": "message",
                "purpose": "explanation",
                "message": "We are reviewing your delivery request.",
                "expected_version": claim["expected_version"] + 1,
                "claim_token": claim["claim_token"],
            }
            stale = await s.call_tool(
                "submit_action",
                {"room_id": room_id, "action": action, "idempotency_key": str(uuid4())},
                raise_on_error=False,
            )
            assert stale.is_error
            action["expected_version"] = claim["expected_version"]
            key = str(uuid4())
            result = await s.call_tool(
                "submit_action", {"room_id": room_id, "action": action, "idempotency_key": key}
            )
            again = await s.call_tool(
                "submit_action", {"room_id": room_id, "action": action, "idempotency_key": key}
            )
            assert result.structured_content == again.structured_content

    asyncio.run(run())
