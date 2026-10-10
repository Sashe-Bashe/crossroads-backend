"""A fixed-origin API client, never an arbitrary URL/HTTP tool."""

import httpx
from fastmcp.exceptions import ToolError

from .config import Configuration, ConfigurationError


class Marketplace:
    def __init__(self, config: Configuration, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.http = httpx.AsyncClient(
            base_url=config.api_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + config.token, "X-Workspace-ID": config.workspace_id},
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, *, params=None, data=None, idempotency_key=None):
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        try:
            response = await self.http.request(method, path, params=params, json=data, headers=headers)
        except httpx.HTTPError:
            raise ToolError(
                "Marketplace unavailable. Check the backend and retry reads when it recovers."
            ) from None
        try:
            body = response.json()
        except ValueError:
            raise ToolError("Marketplace returned an invalid response.") from None
        if not isinstance(body, dict) or len(body) != 2 or body.get("status_code") != response.status_code:
            raise ToolError("Marketplace returned an invalid resource envelope.")
        label = next(key for key in body if key != "status_code")
        resource = body[label]
        if not isinstance(resource, dict):
            raise ToolError("Marketplace returned an invalid resource object.")
        if response.is_error and label == "error":
            # The backend deliberately sanitizes messages. Never include request headers or bodies.
            code = str(resource.get("code", "request_failed"))[:80]
            message = str(resource.get("message", "Marketplace request failed."))[:400]
            raise ToolError(f"{code}: {message}")
        if not response.is_success or label == "error":
            raise ToolError("Marketplace returned an unexpected status; redirects are not followed.")
        return resource

    async def validate_identity(self):
        identity = await self.request("GET", "auth/me")
        if (
            identity.get("credential", {}).get("kind") != "agent"
            or identity.get("workspace", {}).get("id") != self.config.workspace_id
            or identity.get("workspace", {}).get("role") not in {"buyer", "seller"}
            or identity.get("can_approve") is not False
        ):
            raise ConfigurationError("MCP requires a restricted agent bound to the configured workspace.")
        return identity
