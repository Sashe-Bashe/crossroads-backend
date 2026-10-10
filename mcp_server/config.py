"""Explicit process configuration; credentials never enter tool arguments."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Configuration:
    api_url: str
    workspace_id: str
    token: str = field(repr=False)

    def __post_init__(self):
        parsed = urlsplit(self.api_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (parsed.scheme != "https" and not loopback)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api/v1"
        ):
            raise ConfigurationError(
                "NEOPACT_MCP_API_URL must be an HTTPS /api/v1 URL; HTTP is allowed only on loopback."
            )
        if (
            not self.workspace_id
            or len(self.workspace_id) > 100
            or any(c.isspace() for c in self.workspace_id)
        ):
            raise ConfigurationError("Set NEOPACT_MCP_WORKSPACE to one workspace ID.")
        if (
            not self.token.startswith("am_")
            or not 40 <= len(self.token) <= 200
            or any(c.isspace() for c in self.token)
        ):
            raise ConfigurationError("Set NEOPACT_MCP_TOKEN to a restricted workspace agent credential.")

    @classmethod
    def from_environment(cls):
        return cls(
            api_url=os.environ.get("NEOPACT_MCP_API_URL", ""),
            workspace_id=os.environ.get("NEOPACT_MCP_WORKSPACE", ""),
            token=os.environ.get("NEOPACT_MCP_TOKEN", ""),
        )
