"""Provider identity sign-in; no provider token grants marketplace permissions.

Authlib performs OAuth/PKCE and signed OIDC token validation. Transactions live in
PostgreSQL, are bound to an HttpOnly browser cookie and consumed before exchange.
External identities are linked explicitly to an existing authenticated account.
"""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode, urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, delete, select
from sqlalchemy.exc import IntegrityError

from .auth import Actor, actor, digest, throttle
from .config import settings
from .db import Base, now, session, uid
from .errors import require
from .models import Credential, User
from .responses import DTO, ERROR_RESPONSES, ErrorResponse

router = APIRouter(
    prefix="/api/v1/identity",
    tags=["Identity"],
    responses={
        **ERROR_RESPONSES,
        503: {
            "model": ErrorResponse,
            "description": "The provider is unconfigured or temporarily unavailable.",
        },
    },
)
IDENTITY_JSON_OPERATIONS = {"identity_providers", "identity_link"}
IDENTITY_REDIRECT_OPERATIONS = {"identity_login", "identity_callback"}
TRANSACTION_COOKIE = "market_identity_transaction"
TRANSACTION_SECONDS = 600


class IdentityProviderStatus(DTO):
    id: Literal["google", "openai"]
    label: str
    configured: bool
    linked: bool


class IdentityProviderList(DTO):
    providers: list[IdentityProviderStatus]


class IdentityProvidersResponse(DTO):
    identity: IdentityProviderList
    status_code: Literal[200]


class IdentityLinkDestination(DTO):
    redirect_url: str


class IdentityLinkResponse(DTO):
    identity: IdentityLinkDestination
    status_code: Literal[200]


class IdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    google_client_id: str = ""
    google_client_secret: str = ""
    openai_signin_client_id: str = ""
    openai_signin_client_secret: str = ""
    openai_signin_token_endpoint_auth_method: str = "client_secret_post"


identity_settings = IdentitySettings()


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    issuer = Column(String, nullable=False)
    client_id = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("issuer", "client_id", "subject", name="uq_external_identity"),)


class OAuthTransaction(Base):
    __tablename__ = "oauth_transactions"
    id = Column(String, primary_key=True, default=uid)
    browser_hash = Column(String, nullable=False, unique=True)
    state_hash = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    client_id = Column(String, nullable=False)
    nonce = Column(String, nullable=False)
    code_verifier = Column(String, nullable=False)
    redirect_uri = Column(String, nullable=False)
    link_user_id = Column(String, ForeignKey("users.id"))
    link_credential_id = Column(String, ForeignKey("credentials.id"))
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    issuer: str
    discovery_url: str
    client_id: str
    client_secret: str
    auth_method: str

    @property
    def configured(self):
        return bool(
            self.client_id and (self.auth_method == "none" or self.client_secret)
        ) and self.auth_method in (
            "none",
            "client_secret_post",
            "client_secret_basic",
        )


def providers():
    return {
        "google": Provider(
            "google",
            "Google",
            "https://accounts.google.com",
            "https://accounts.google.com/.well-known/openid-configuration",
            identity_settings.google_client_id,
            identity_settings.google_client_secret,
            "client_secret_post",
        ),
        "openai": Provider(
            "openai",
            "ChatGPT",
            "https://auth.openai.com",
            "https://auth.openai.com/.well-known/openid-configuration",
            identity_settings.openai_signin_client_id,
            identity_settings.openai_signin_client_secret,
            identity_settings.openai_signin_token_endpoint_auth_method,
        ),
    }


def provider_for(name):
    provider = providers().get(name)
    require(provider is not None, "identity_provider_unknown", "Unknown sign-in provider", 404)
    require(
        provider.configured, "identity_provider_unavailable", "This sign-in provider is not configured", 503
    )
    return provider


def frontend_origin():
    url = urlsplit(settings.frontend_url)
    # Never derive redirects from untrusted Host/Forwarded headers or a return_to parameter.
    valid_transport = url.scheme == "https" or (
        url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
    )
    require(
        valid_transport
        and bool(url.netloc)
        and not url.username
        and not url.password
        and url.path in ("", "/")
        and not url.query
        and not url.fragment,
        "identity_configuration_invalid",
        "Identity sign-in requires a configured frontend origin",
        503,
    )
    return f"{url.scheme}://{url.netloc}"


def oauth_client(provider):
    oauth = OAuth()
    return oauth.register(
        provider.id,
        client_id=provider.client_id,
        client_secret=provider.client_secret or None,
        server_metadata_url=provider.discovery_url,
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": provider.auth_method,
            "timeout": 15.0,
        },
    )


async def checked_client(provider):
    client = oauth_client(provider)
    metadata = await client.load_server_metadata()
    if metadata.get("issuer") != provider.issuer:
        raise ValueError("Unexpected provider issuer")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = urlsplit(metadata.get(key, ""))
        if endpoint.scheme != "https" or not endpoint.netloc or endpoint.username or endpoint.password:
            raise ValueError("Invalid provider endpoint")
    # Only asymmetric signatures; never permit HS* or 'none' from discovery.
    supported = metadata.get("id_token_signing_alg_values_supported", ["RS256"])
    metadata["id_token_signing_alg_values_supported"] = [
        value
        for value in supported
        if value in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA")
    ]
    if not metadata["id_token_signing_alg_values_supported"]:
        raise ValueError("No supported signature algorithm")
    return client


def browser_credential(request, db):
    token = request.cookies.get("market_session")
    if not token:
        return None
    credential = db.scalar(select(Credential).where(Credential.token_hash == digest(token)))
    if not credential or credential.kind != "browser" or credential.revoked or credential.expires_at <= now():
        return None
    user = db.get(User, credential.user_id)
    return credential if user and user.active else None


@router.get(
    "/providers",
    operation_id="identity_providers",
    response_model=IdentityProvidersResponse,
    description="Read provider configuration and the current browser account's linked status. Secrets and external subject identifiers are never returned.",
    openapi_extra={"security": [], "x-object-label": "identity"},
)
def identity_providers(request: Request, db=Depends(session)):
    credential = browser_credential(request, db)
    mappings = (
        list(db.scalars(select(ExternalIdentity).where(ExternalIdentity.user_id == credential.user_id)))
        if credential
        else []
    )
    return {
        "identity": {
            "providers": [
                {
                    "id": provider.id,
                    "label": provider.label,
                    "configured": provider.configured,
                    "linked": any(
                        row.issuer == provider.issuer and row.client_id == provider.client_id
                        for row in mappings
                    ),
                }
                for provider in providers().values()
            ]
        },
        "status_code": 200,
    }


async def begin(provider_name, request, db, linking=None):
    provider = provider_for(provider_name)
    redirect_uri = f"{frontend_origin()}/api/v1/identity/{provider.id}/callback"
    throttle(db, "identity-start:" + (request.client.host if request.client else "unknown"), 20)
    db.commit()
    state, browser_id, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(4))
    try:
        client = await checked_client(provider)
        authorization = await client.create_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )
    except Exception:
        # Provider errors may contain codes/tokens. Never expose or log them.
        require(
            False, "identity_provider_unavailable", "The sign-in provider is unavailable. Try again.", 503
        )
    old_browser_id = request.cookies.get(TRANSACTION_COOKIE)
    db.execute(delete(OAuthTransaction).where(OAuthTransaction.expires_at <= now()))
    if old_browser_id:
        db.execute(delete(OAuthTransaction).where(OAuthTransaction.browser_hash == digest(old_browser_id)))
    db.add(
        OAuthTransaction(
            browser_hash=digest(browser_id),
            state_hash=digest(state),
            provider=provider.id,
            client_id=provider.client_id,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            link_user_id=linking.user.id if linking else None,
            link_credential_id=linking.credential.id if linking else None,
            expires_at=now() + timedelta(seconds=TRANSACTION_SECONDS),
        )
    )
    db.commit()
    response = (
        JSONResponse({"identity": {"redirect_url": authorization["url"]}, "status_code": 200})
        if linking
        else RedirectResponse(authorization["url"], status_code=303)
    )
    response.set_cookie(
        TRANSACTION_COOKIE,
        browser_id,
        max_age=TRANSACTION_SECONDS,
        httponly=True,
        secure=settings.cookie_secure or frontend_origin().startswith("https:"),
        samesite="lax",
        path="/api/v1/identity",
    )
    return response


@router.get(
    "/{provider}/login",
    operation_id="identity_login",
    response_class=RedirectResponse,
    status_code=303,
    description="Create a ten-minute browser-bound OIDC transaction and redirect to the configured provider using state, nonce and S256 PKCE.",
    openapi_extra={"security": [], "x-response-format": "redirect"},
)
async def identity_login(provider: str, request: Request, db=Depends(session)):
    return await begin(provider, request, db)


@router.post(
    "/{provider}/link",
    operation_id="identity_link",
    response_model=IdentityLinkResponse,
    description="Start an explicit provider link for the current browser account. Requires an allowed Origin and matching CSRF token; no email-based account linking.",
    openapi_extra={
        "security": [{"BrowserSession": []}],
        "x-object-label": "identity",
        "parameters": [
            {"name": "Origin", "in": "header", "required": True, "schema": {"type": "string"}},
            {"name": "X-CSRF-Token", "in": "header", "required": True, "schema": {"type": "string"}},
        ],
    },
)
async def identity_link(provider: str, request: Request, a: Actor = Depends(actor)):
    require(
        a.browser and a.credential.kind == "browser",
        "browser_session_required",
        "Link from a signed-in browser",
        403,
    )
    return await begin(provider, request, a.db, linking=a)


def callback_redirect(key, value):
    response = RedirectResponse(
        f"{frontend_origin()}/?{urlencode({'view': 'connections', key: value})}", status_code=303
    )
    response.delete_cookie(
        TRANSACTION_COOKIE,
        path="/api/v1/identity",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure or frontend_origin().startswith("https:"),
    )
    return response


def consume_transaction(request, db):
    browser_id = request.cookies.get(TRANSACTION_COOKIE)
    if not browser_id:
        return None
    transaction = db.scalar(
        select(OAuthTransaction).where(OAuthTransaction.browser_hash == digest(browser_id)).with_for_update()
    )
    if transaction:
        db.delete(transaction)
        # Commit before network I/O so a callback cannot exchange the same transaction twice.
        db.commit()
    return transaction


def issue_session(db, user, response, previous=None):
    if previous:
        previous.revoked = True
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(
        Credential(
            token_hash=digest(token),
            user_id=user.id,
            name="browser session",
            kind="browser",
            scopes=[],
            csrf_hash=digest(csrf),
            expires_at=now() + timedelta(hours=12),
        )
    )
    db.commit()
    secure = settings.cookie_secure or frontend_origin().startswith("https:")
    response.set_cookie(
        "market_session", token, httponly=True, secure=secure, samesite="strict", max_age=43200, path="/"
    )
    response.set_cookie(
        "market_csrf", csrf, httponly=False, secure=secure, samesite="strict", max_age=43200, path="/"
    )


@router.get(
    "/{provider}/callback",
    operation_id="identity_callback",
    response_class=RedirectResponse,
    status_code=303,
    description="Consume one browser-bound transaction, verify the provider ID token and create a Neopact session for an explicitly linked identity. Redirects to the configured frontend with stable result codes.",
    openapi_extra={"security": [], "x-response-format": "redirect"},
)
async def identity_callback(provider: str, request: Request, db=Depends(session)):
    transaction = consume_transaction(request, db)
    state = request.query_params.get("state", "")
    configured = providers().get(provider)
    valid = (
        transaction is not None
        and configured is not None
        and configured.configured
        and transaction.provider == provider
        and transaction.client_id == configured.client_id
        and transaction.redirect_uri == f"{frontend_origin()}/api/v1/identity/{provider}/callback"
        and transaction.expires_at > now()
        and bool(state)
        and secrets.compare_digest(transaction.state_hash, digest(state))
    )
    if not valid:
        return callback_redirect("identity_error", "invalid_transaction")
    if request.query_params.get("error"):
        return callback_redirect("identity_error", "provider_declined")
    code = request.query_params.get("code")
    if not code or len(code) > 4096:
        return callback_redirect("identity_error", "invalid_transaction")
    try:
        client = await checked_client(configured)
        token = await client.fetch_access_token(
            redirect_uri=transaction.redirect_uri, code=code, code_verifier=transaction.code_verifier
        )
        claims = await client.parse_id_token(
            token,
            nonce=transaction.nonce,
            leeway=30,
            claims_options={
                "iss": {"essential": True, "value": configured.issuer},
                "aud": {"essential": True, "value": configured.client_id},
            },
        )
        # Exact checks also reject providers that disable nonce verification via nonce_supported=false.
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        subject = claims.get("sub")
        if (
            claims.get("iss") != configured.issuer
            or configured.client_id not in audiences
            or claims.get("nonce") != transaction.nonce
            or not isinstance(subject, str)
            or not subject
            or len(subject) > 255
        ):
            raise ValueError("Invalid identity claims")
    except Exception:
        return callback_redirect("identity_error", "verification_failed")
    identity = db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == configured.issuer,
            ExternalIdentity.client_id == configured.client_id,
            ExternalIdentity.subject == subject,
        )
    )
    previous = browser_credential(request, db)
    if transaction.link_user_id:
        linking = db.get(Credential, transaction.link_credential_id)
        user = db.get(User, transaction.link_user_id)
        if (
            not linking
            or linking.kind != "browser"
            or linking.revoked
            or linking.expires_at <= now()
            or linking.user_id != transaction.link_user_id
            or not user
            or not user.active
            or (previous and previous.user_id != user.id)
        ):
            return callback_redirect("identity_error", "link_session_expired")
        if identity and identity.user_id != user.id:
            return callback_redirect("identity_error", "identity_already_linked")
        if not identity:
            db.add(
                ExternalIdentity(
                    user_id=user.id,
                    provider=configured.id,
                    issuer=configured.issuer,
                    client_id=configured.client_id,
                    subject=subject,
                )
            )
        previous = linking
        result = "linked"
    else:
        if not identity:
            return callback_redirect("identity_error", "account_not_linked")
        user = db.get(User, identity.user_id)
        if not user or not user.active:
            return callback_redirect("identity_error", "account_unavailable")
        result = "signed_in"
    response = callback_redirect("identity", result)
    try:
        issue_session(db, user, response, previous)
    except IntegrityError:
        db.rollback()
        return callback_redirect("identity_error", "identity_already_linked")
    return response
