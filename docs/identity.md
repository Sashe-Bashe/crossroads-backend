# Website sign-in with Google and ChatGPT

Neopact can link an existing business account to Google or ChatGPT and subsequently use that identity to sign in. Provider identity never grants a new workspace membership, approves an offer, or authorizes an agent. Password sign-in remains available. There is deliberately no email-based account merge or automatic business creation.

The implementation uses Authlib for authorization code flow, S256 PKCE and signed OpenID Connect ID-token validation. Transactions live in PostgreSQL and are consumed atomically before a code exchange; they are bound to a separate ten-minute HttpOnly, SameSite=Lax browser cookie. Application sessions remain HttpOnly and SameSite=Strict. Use HTTPS and `COOKIE_SECURE=true` outside loopback development.

## Configuration

Apply `alembic upgrade head` before serving the identity routes. Set provider credentials in the backend environment or its local ignored `.env` file. They must never be copied to frontend public environment variables, source control or browser code.

```dotenv
FRONTEND_URL=http://127.0.0.1:3187
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
OPENAI_SIGNIN_CLIENT_ID=
OPENAI_SIGNIN_CLIENT_SECRET=
OPENAI_SIGNIN_TOKEN_ENDPOINT_AUTH_METHOD=client_secret_post
```

Register **exact** callbacks for each environment:

- Google: `{FRONTEND_URL}/api/v1/identity/google/callback`
- ChatGPT: `{FRONTEND_URL}/api/v1/identity/openai/callback`

`FRONTEND_URL` must be an HTTPS origin or loopback HTTP origin without a path, query or fragment. Redirects never derive from a request Host or Forwarded header. The frontend proxy must forward `Location` and every `Set-Cookie` header and permit POST on the two provider link routes.

Google requires an OAuth web-application client ID and secret. For ChatGPT, use the client ID and token-endpoint authentication method supplied by OpenAI. A provisioned public client uses `OPENAI_SIGNIN_TOKEN_ENDPOINT_AUTH_METHOD=none` and no secret. Confidential clients use their registered `client_secret_post` or `client_secret_basic` method.

As of October 10, 2026, website Sign in with ChatGPT is a limited trial for selected commercial partners. A ChatGPT subscription or an API key does not provision this website OAuth client. Provider configuration is complete only after the operator supplies registered credentials and callbacks. See [OpenAI's website integration guide](https://developers.openai.com/siwc/website) and [Google's OpenID Connect guide](https://developers.google.com/identity/openid-connect/openid-connect).

## Browser integration

1. Read `GET /api/v1/identity/providers`. It returns:

   ```json
   {"identity":{"providers":[{"id":"google","label":"Google","configured":false,"linked":false},{"id":"openai","label":"ChatGPT","configured":false,"linked":false}]},"status_code":200}
   ```

   The endpoint is available before sign-in. `linked` is true only for mappings belonging to a valid current browser session. Neither client secrets nor external subject IDs appear in this response. An unavailable provider should show a setup status, never a working-looking sign-in button.

2. To link for the first time, sign in with the existing Neopact account, then POST `/api/v1/identity/{provider}/link` with the normal browser Origin and CSRF header. It returns `{"identity":{"redirect_url":"https://…"},"status_code":200}` and sets the transaction cookie. Navigate the same browser to `redirect_url`.
3. To sign in after linking, navigate to `GET /api/v1/identity/{provider}/login`. The server creates a fresh transaction and redirects directly to the provider.
4. Callback success returns to `/?view=connections&identity=linked` or `identity=signed_in`, issues a fresh Neopact browser session/CSRF cookie, and revokes the session it replaced where available. Clear the frontend's cached workspace selection and load `/auth/me` again.
5. Callback failures return a stable `identity_error` query value. Never display raw provider errors. Show an actionable message and let the user start again.

Error values are `invalid_transaction`, `provider_declined`, `verification_failed`, `account_not_linked`, `identity_already_linked`, `link_session_expired`, and `account_unavailable`. Unknown provider starts return 404; unconfigured or temporarily unavailable starts return 503. The provider identifiers are `google` and `openai`.

The first link requires the user's existing authenticated browser session and CSRF protection. The callback checks that the original linking session remains active. An external identity is bound by the immutable tuple `(issuer, client_id, subject)`; matching email addresses do not link accounts. Reconfiguring a client ID intentionally requires a new explicit account link.

## Stored information and boundaries

`external_identities` retains only the account mapping, provider ID, issuer, client ID, subject and creation timestamp. `oauth_transactions` retains short-lived state hashes, browser binding, nonce, PKCE verifier, exact redirect and the optional linking session. Expired transactions are removed on subsequent sign-in starts; consumed transactions are deleted before network exchange. Access/refresh/ID tokens and authorization codes are never stored or logged by this integration. The provider access token is discarded after validating the ID token.

The middleware already suppresses credential-bearing exception details. Deploy behind access logging that omits callback query strings; authorization codes appear in the browser callback URL before the immediate clean redirect. Authlib validates signatures, expiration, issuer, audience and nonce; the application additionally pins provider issuers, accepts only asymmetric signature algorithms and checks the exact nonce even if a provider tries to disable nonce verification.

MCP connection authorization is a separate flow. Linking Google or ChatGPT identifies the person; it does not connect an MCP client or grant trading scopes.

## Verification

`pytest tests/test_identity.py` exercises real isolated PostgreSQL transaction/session storage and Authlib verification of RSA-signed test tokens. It covers explicit linking, no email auto-linking, membership preservation, CSRF and browser-only linking, state/cookie/expiry/provider/client binding, bad signature/claims, disabled users, revoked linking sessions, single-use concurrent callbacks, secret suppression and session rotation. Live provider sign-in still requires registered provider credentials and an operator smoke test.

Reference: [Authlib Starlette integration](https://docs.authlib.org/en/stable/client/starlette.html) and [Authlib HTTP clients / PKCE](https://docs.authlib.org/en/stable/oauth2/client/http/index.html).
