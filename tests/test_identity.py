"""Real transaction/session storage and Authlib signed-token verification with a mock IdP."""

import base64
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.starlette_client import OAuth
from fastapi.testclient import TestClient
from joserfc import jwk, jwt
from sqlalchemy import func, select
from test_marketplace import BASE, ORIGIN, browser, user

from market import identity
from market.auth import digest
from market.config import settings
from market.db import now
from market.main import app
from market.models import Credential, User

ROOT = BASE + "/identity"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(identity.identity_settings, "google_client_id", "neopact-test-client")
    monkeypatch.setattr(identity.identity_settings, "google_client_secret", "test-secret-never-in-browser")
    monkeypatch.setattr(identity.identity_settings, "openai_signin_client_id", "")
    monkeypatch.setattr(settings, "frontend_url", ORIGIN)
    key = jwk.RSAKey.generate_key(2048, parameters={"kid": "primary"})
    bad_key = jwk.RSAKey.generate_key(2048, parameters={"kid": "primary"})
    state = {"claims": {}, "exchanges": [], "key": key, "bad_key": bad_key}

    def create(config):
        oauth = OAuth()
        client = oauth.register(
            config.id,
            client_id=config.client_id,
            client_secret=config.client_secret,
            issuer=config.issuer,
            authorization_endpoint="https://identity.test/authorize",
            token_endpoint="https://identity.test/token",
            jwks_uri="https://identity.test/jwks",
            jwks={"keys": [key.as_dict(private=False)]},
            id_token_signing_alg_values_supported=["RS256"],
            client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256"},
        )
        real_authorize = client.create_authorization_url

        async def authorize(**kwargs):
            result = await real_authorize(**kwargs)
            state["authorization"] = {**kwargs, "url": result["url"]}
            return result

        async def exchange(**kwargs):
            state["exchanges"].append(kwargs)
            if state.get("exchange_error"):
                raise ValueError("provider-error-containing-secret-access-token")
            claims = {
                "iss": config.issuer,
                "sub": "external-stable-subject",
                "aud": config.client_id,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "nonce": state["authorization"]["nonce"],
                "email": "buyer@demo.local",
                "email_verified": True,
                **state["claims"],
            }
            token = jwt.encode({"alg": "RS256", "kid": "primary"}, claims, state.get("sign_key", key))
            return {"access_token": "secret-access-token", "id_token": token, "token_type": "Bearer"}

        client.create_authorization_url = authorize
        client.fetch_access_token = exchange
        return client

    monkeypatch.setattr(identity, "oauth_client", create)
    return state


def start(client, link=False):
    response = (
        client.post(ROOT + "/google/link")
        if link
        else client.get(ROOT + "/google/login", follow_redirects=False)
    )
    assert response.status_code == (200 if link else 303), response.text
    location = response.json()["identity"]["redirect_url"] if link else response.headers["location"]
    return parse_qs(urlsplit(location).query), response


def callback(client, query, **extra):
    return client.get(
        ROOT + "/google/callback",
        params={"state": query["state"][0], "code": "one-use-code", **extra},
        follow_redirects=False,
    )


def result(response):
    assert response.status_code == 303, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)


def add_link(
    factory,
    account="buyer",
    subject="external-stable-subject",
    issuer="https://accounts.google.com",
    client_id="neopact-test-client",
):
    with factory.begin() as db:
        db.add(
            identity.ExternalIdentity(
                user_id=account, provider="google", issuer=issuer, client_id=client_id, subject=subject
            )
        )


def test_provider_status_does_not_expose_credentials_or_links_to_anonymous(system, provider):
    client, factory = system
    add_link(factory)
    status = client.get(ROOT + "/providers")
    assert status.json() == {
        "identity": {
            "providers": [
                {"id": "google", "label": "Google", "configured": True, "linked": False},
                {"id": "openai", "label": "ChatGPT", "configured": False, "linked": False},
            ]
        },
        "status_code": 200,
    }
    authenticated = browser("buyer")
    assert authenticated.get(ROOT + "/providers").json()["identity"]["providers"][0]["linked"]
    assert "test-secret" not in status.text and "external-stable-subject" not in status.text
    assert client.get(ROOT + "/openai/login", follow_redirects=False).status_code == 503
    assert client.get(ROOT + "/unknown/login", follow_redirects=False).status_code == 404


def test_pkce_nonce_state_are_server_bound_and_callback_is_configured_origin(system, provider):
    client, factory = system
    client.headers["X-Forwarded-Host"] = "attacker.example"
    query, response = start(client)
    assert query["redirect_uri"] == [ORIGIN + ROOT + "/google/callback"]
    assert query["response_type"] == ["code"] and query["code_challenge_method"] == ["S256"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=600" in cookie
    with factory() as db:
        transaction = db.scalar(select(identity.OAuthTransaction))
        assert transaction.browser_hash == digest(client.cookies.get(identity.TRANSACTION_COOKIE))
        assert transaction.state_hash == digest(query["state"][0])
        assert transaction.nonce == query["nonce"][0]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(transaction.code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert query["code_challenge"] == [challenge]
        assert transaction.code_verifier not in cookie
        assert transaction.nonce not in cookie
    assert "test-secret" not in response.headers["location"]


def test_login_never_auto_links_matching_email_or_creates_accounts(system, provider):
    client, factory = system
    query, _ = start(client)
    assert result(callback(client, query))["identity_error"] == ["account_not_linked"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(identity.ExternalIdentity)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 4
        assert db.scalar(select(func.count()).select_from(Credential)) == 0
        assert db.scalar(select(func.count()).select_from(identity.OAuthTransaction)) == 0


def test_link_requires_browser_origin_and_csrf(system, provider):
    client, _ = system
    assert client.post(ROOT + "/google/link").status_code == 401
    assert client.post(ROOT + "/google/link", headers=user(client, "buyer")).status_code == 403
    b = browser("buyer")
    csrf = b.headers.pop("X-CSRF-Token")
    assert b.post(ROOT + "/google/link").status_code == 403
    b.headers["X-CSRF-Token"] = csrf
    b.headers["Origin"] = "https://attacker.example"
    assert b.post(ROOT + "/google/link").status_code == 403


def test_explicit_link_then_login_rotates_session_without_retaining_provider_tokens(system, provider):
    _, factory = system
    b = browser("buyer")
    old_token = b.cookies.get("market_session")
    query, _ = start(b, link=True)
    # Strict application cookies are absent on a real cross-site IdP callback.
    b.cookies.delete("market_session")
    assert result(callback(b, query))["identity"] == ["linked"]
    assert b.cookies.get("market_session") and b.cookies.get("market_session") != old_token
    with factory() as db:
        link = db.scalar(select(identity.ExternalIdentity))
        assert link.user_id == "buyer" and link.subject == "external-stable-subject"
        assert db.scalar(select(Credential).where(Credential.token_hash == digest(old_token))).revoked
        credentials = list(db.scalars(select(Credential)))
        assert all(c.kind == "browser" and c.scopes == [] and c.workspace_id is None for c in credentials)
        assert all(c.token_hash != digest("secret-access-token") for c in credentials)
    current = b.cookies.get("market_session")
    query, _ = start(b)
    assert result(callback(b, query))["identity"] == ["signed_in"]
    assert b.cookies.get("market_session") != current
    assert provider["exchanges"][-1]["redirect_uri"] == ORIGIN + ROOT + "/google/callback"
    assert provider["exchanges"][-1]["code_verifier"] == provider["authorization"]["code_verifier"]
    assert b.get(BASE + "/auth/me").json()["identity"]["workspace"]["id"] == "ws-buyer"


@pytest.mark.parametrize(
    "failure", ["state", "cookie", "expired", "provider", "client_changed", "denied", "missing_code"]
)
def test_invalid_callbacks_do_not_exchange_and_consume_bound_transactions(
    system, provider, failure, monkeypatch
):
    client, factory = system
    query, _ = start(client)
    params = {"state": query["state"][0], "code": "one-use-code"}
    path = ROOT + "/google/callback"
    if failure == "state":
        params["state"] = "attacker-state"
    elif failure == "cookie":
        client.cookies.clear()
    elif failure == "expired":
        with factory.begin() as db:
            db.scalar(select(identity.OAuthTransaction)).expires_at = now() - timedelta(seconds=1)
    elif failure == "provider":
        path = ROOT + "/openai/callback"
    elif failure == "client_changed":
        monkeypatch.setattr(identity.identity_settings, "google_client_id", "rotated-client")
    elif failure == "denied":
        params.update(error="access_denied", error_description="private-error-payload")
    elif failure == "missing_code":
        params.pop("code")
    response = client.get(path, params=params, follow_redirects=False)
    expected = "provider_declined" if failure == "denied" else "invalid_transaction"
    assert result(response)["identity_error"] == [expected]
    assert "private-error-payload" not in response.headers["location"]
    assert provider["exchanges"] == []
    if failure != "cookie":
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(identity.OAuthTransaction)) == 0


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example"},
        {"aud": "other-client"},
        {"aud": ["other-client"], "azp": "neopact-test-client"},
        {"nonce": "wrong-nonce"},
        {"nonce": "wrong-nonce", "nonce_supported": False},
        {"sub": ""},
        {"exp": 1},
        {"iat": int(time.time()) + 3600},
        {"azp": "other-client"},
    ],
)
def test_invalid_signed_claims_cannot_create_marketplace_session(system, provider, claims):
    client, factory = system
    add_link(factory)
    provider["claims"] = claims
    query, _ = start(client)
    assert result(callback(client, query))["identity_error"] == ["verification_failed"]
    assert not client.cookies.get("market_session")
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Credential)) == 0


def test_wrong_signature_and_provider_errors_are_sanitized(system, provider):
    client, factory = system
    add_link(factory)
    provider["sign_key"] = provider["bad_key"]
    query, _ = start(client)
    assert result(callback(client, query))["identity_error"] == ["verification_failed"]
    provider["exchange_error"] = True
    query, _ = start(client)
    response = callback(client, query)
    assert result(response)["identity_error"] == ["verification_failed"]
    assert "secret-access-token" not in response.text + str(response.headers)


def test_already_linked_identity_cannot_move_accounts(system, provider):
    _, factory = system
    add_link(factory, "forma")
    b = browser("buyer")
    query, _ = start(b, link=True)
    assert result(callback(b, query))["identity_error"] == ["identity_already_linked"]
    with factory() as db:
        assert db.scalar(select(identity.ExternalIdentity)).user_id == "forma"


@pytest.mark.parametrize("failure", ["revoked", "disabled"])
def test_link_session_must_remain_active(system, provider, failure):
    _, factory = system
    b = browser("buyer")
    query, _ = start(b, link=True)
    with factory.begin() as db:
        if failure == "revoked":
            db.scalar(
                select(Credential).where(Credential.token_hash == digest(b.cookies.get("market_session")))
            ).revoked = True
        else:
            db.get(User, "buyer").active = False
    assert result(callback(b, query))["identity_error"] == ["link_session_expired"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(identity.ExternalIdentity)) == 0


def test_disabled_linked_user_cannot_sign_in(system, provider):
    client, factory = system
    add_link(factory)
    with factory.begin() as db:
        db.get(User, "buyer").active = False
    query, _ = start(client)
    assert result(callback(client, query))["identity_error"] == ["account_unavailable"]
    assert not client.cookies.get("market_session")


def test_simultaneous_callback_is_consumed_once(system, provider):
    client, factory = system
    add_link(factory)
    query, _ = start(client)
    cookie = client.cookies.get(identity.TRANSACTION_COOKIE)

    def exchange(_):
        separate = TestClient(app)
        separate.cookies.set(identity.TRANSACTION_COOKIE, cookie, path="/api/v1/identity")
        return result(callback(separate, query))

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(exchange, range(2)))
    assert sum(row.get("identity") == ["signed_in"] for row in responses) == 1
    assert sum(row.get("identity_error") == ["invalid_transaction"] for row in responses) == 1
    assert len(provider["exchanges"]) == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Credential)) == 1
