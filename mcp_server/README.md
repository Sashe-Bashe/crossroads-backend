# Neopact MCP server

A working local MCP server for a buyer or seller agent, backed by the same API as the dashboard. It uses FastMCP 3 and the backend's existing schemas, permissions, negotiation leases, version checks and idempotency receipts. It does not simulate trades or run a language model.

## Install and run

Start the existing backend first, then install the optional MCP dependency:

```sh
uv sync --extra mcp
```

In the dashboard's **Connections** view, create a restricted agent credential for the selected workspace. Start with the read scopes `catalogue:read`, `images:read`, `negotiations:read` and `orders:read`. Add `negotiations:act` only if the host should negotiate, and `requests:write` for buyer requests or `products:write` for seller catalogue edits only when needed. Existing CLI credential issuance also works.

The server requires three environment variables:

| Variable | Meaning |
| --- | --- |
| `NEOPACT_MCP_API_URL` | Backend API root, such as `http://127.0.0.1:8100/api/v1` |
| `NEOPACT_MCP_WORKSPACE` | Exact workspace ID from the issued credential |
| `NEOPACT_MCP_TOKEN` | Restricted `am_…` agent token, kept in the host's secret environment |

HTTPS is required for non-loopback backends. Browser sessions and administrator CLI credentials are rejected. The token and workspace are checked against `/auth/me` at startup; the backend checks expiry, revocation, membership and scopes again on every call. The server never follows redirects or reads proxy credentials from the environment.

Run with the environment supplied by your MCP host:

```sh
uv run --extra mcp python -m mcp_server
```

Or use the installed entry point:

```sh
uv run --extra mcp neopact-mcp
```

This command speaks MCP over stdin/stdout. An apparently waiting terminal is normal: it is awaiting a host's protocol messages. Diagnostic logs go to stderr. Never paste the token into an assistant conversation, a shell command argument, source control, or a screenshot.

## Connect a local host

Use the absolute path to this backend checkout for `/absolute/path/crossroads-backend` below. Each buyer/seller connection runs in its own process with its own restricted credential. The configured agent cannot switch workspace.

### Claude Code

Supply the three environment variables securely in the launching process, then register:

```sh
claude mcp add --transport stdio neopact -- uv --directory /absolute/path/crossroads-backend run --extra mcp python -m mcp_server
```

Restart the host when changing its inherited credential environment. Read-only credentials can list/read data; write tool calls are denied until the required scope has explicitly been granted through a newly issued credential.

### Claude Desktop

Desktop apps may not inherit your terminal's environment. A local launcher can read the token from the operating system's secret store. For example, after saving the credential in macOS Keychain with service name `neopact-buyer-agent`, a private launcher script can contain:

```sh
#!/bin/sh
set -eu
export NEOPACT_MCP_API_URL='http://127.0.0.1:8100/api/v1'
export NEOPACT_MCP_WORKSPACE='ws-buyer'
NEOPACT_MCP_TOKEN="$(/usr/bin/security find-generic-password -s neopact-buyer-agent -w)"
export NEOPACT_MCP_TOKEN
exec /opt/homebrew/bin/uv --directory /absolute/path/crossroads-backend run --extra mcp python -m mcp_server
```

The script contains the keychain item name, not its secret. Grant the script executable permission. Register it in the desktop MCP configuration:

```json
{
  "mcpServers": {
    "neopact-buyer": {
      "command": "/absolute/path/neopact-buyer-launcher"
    }
  }
}
```

Use a different keychain item and workspace ID for the seller connection. Other operating systems should use their equivalent secret store or the host's protected environment facility.

### Codex

With the token provided in the host's inherited environment, use a stdio server configuration:

```toml
[mcp_servers.neopact]
command = "uv"
args = ["--directory", "/absolute/path/crossroads-backend", "run", "--extra", "mcp", "python", "-m", "mcp_server"]
env_vars = ["NEOPACT_MCP_TOKEN"]

[mcp_servers.neopact.env]
NEOPACT_MCP_API_URL = "http://127.0.0.1:8100/api/v1"
NEOPACT_MCP_WORKSPACE = "ws-buyer"
```

For a GUI host without an inherited token environment, configure the private secret-store launcher as `command` instead.

## Available tools

| Tools | Required capability |
| --- | --- |
| `get_instructions` | Valid configured agent identity |
| `search_catalogue`, `get_product_images` | Catalogue/image reading |
| `get_inbox`, `get_negotiation_context`, `get_activity` | Negotiation reading |
| `get_orders` | Order reading |
| `create_request`, `launch_request` | Buyer request writing |
| `create_product`, `update_product` | Seller product writing |
| `claim_turn`, `submit_action` | Negotiation actions |

Tool inputs use the backend's `RequestInput`, `ProductInput`, `ProductUpdate` and `ActionInput` models directly. Money is integer EUR cents. Writes require the backend's exact expected version and/or a stable idempotency key. After a timeout, the write outcome is unknown; inspect state or retry the same intended mutation with the same key. A conflict requires fresh context, not an automatic retry against changed terms.

`get_product_images` returns metadata, not image bytes; it must not be described as visual inspection. Use the repository's existing image-reading CLI with an appropriately scoped agent when a task requires viewing the actual images.

No tool can approve an order, edit business rules, waive requirements, issue credentials, change workspace, or make arbitrary HTTP calls. Product changes may invalidate offers. Accepting terms only advances to human review. A human must approve the exact offer in the dashboard; demo orders are not payments.

## Hosted ChatGPT and Claude connections

**This release is local stdio. It does not expose an HTTP endpoint or claim to provide remote OAuth.** ChatGPT's hosted connector and Claude's hosted connector require a separately deployed, reachable Streamable HTTP server and appropriate user authorization.

The remote implementation must validate an MCP-specific token audience and map that authenticated identity to one authorized Neopact workspace. It must not forward the incoming MCP access token to this API or use one environment credential for all remote callers. It needs OAuth protected-resource metadata, authorization-server discovery, authorization code + S256 PKCE, exact redirect validation, token expiry/revocation, scope enforcement, and a durable account/workspace linkage. Use a maintained authorization provider or FastMCP OAuth integration rather than a homemade shared-token gateway.

Website **Sign in with Google/ChatGPT** is a separate identity flow. Signing in to the website does not itself connect an MCP host. Google needs registered application credentials. Sign in with ChatGPT currently needs a registered OpenAI client and commercial trial access. The local server above works without either provider.

Official references checked October 10, 2026:

- [FastMCP server documentation](https://gofastmcp.com/servers/server)
- [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [ChatGPT MCP authentication](https://developers.openai.com/plugins/build/auth)
- [Claude connector authentication](https://claude.com/docs/connectors/building/authentication)
- [Sign in with ChatGPT website integration](https://developers.openai.com/siwc/website)

## Verification

With the existing PostgreSQL container running:

```sh
uv run --extra mcp pytest tests/test_mcp.py -q
```

The backend-backed tests use a private temporary database schema and do not reset or consume the demo market. They check role/workspace isolation, read-only scope denial, credential revocation, structured schemas and annotations, stale versions, idempotency, fixed-origin requests and credential-safe errors.

For interactive local inspection, supply the same environment and use the official MCP Inspector:

```sh
npx @modelcontextprotocol/inspector uv --directory /absolute/path/crossroads-backend run --extra mcp python -m mcp_server
```

Use `tools/list`, then `get_inbox` or `search_catalogue` for a read-only smoke test. Revoke the credential in the dashboard when removing the connection.
