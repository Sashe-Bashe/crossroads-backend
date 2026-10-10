import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .config import settings
from .db import now, session
from .errors import require
from .models import Credential, Membership, RateWindow, User, Workspace

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("unused-constant-time-password")
ROLE_SCOPES = {
    "buyer": {
        "catalogue:read",
        "images:read",
        "requests:write",
        "negotiations:read",
        "negotiations:act",
        "orders:read",
    },
    "seller": {
        "catalogue:read",
        "images:read",
        "products:write",
        "images:write",
        "negotiations:read",
        "negotiations:act",
        "orders:read",
    },
}
ADMIN_SCOPES = {"policies:write", "credentials:manage"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def throttle(db, key, maximum, seconds=60):
    """Database-atomic window shared by all API processes."""
    stamp = now()
    db.execute(
        insert(RateWindow)
        .values(key=key, count=0, reset_at=stamp + timedelta(seconds=seconds))
        .on_conflict_do_nothing(index_elements=[RateWindow.key])
    )
    row = db.scalar(select(RateWindow).where(RateWindow.key == key).with_for_update())
    if row.reset_at <= stamp:
        row.count, row.reset_at = 0, stamp + timedelta(seconds=seconds)
    require(row.count < maximum, "rate_limited", "Too many requests. Retry after the current window.", 429)
    row.count += 1


def login(db, email, password, kind):
    user = db.scalar(select(User).where(User.email == email.lower()))
    valid = passwords.verify(password, user.password_hash if user else DUMMY_HASH)
    require(
        user is not None and user.active and valid, "invalid_credentials", "Invalid email or password", 401
    )
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    credential = Credential(
        token_hash=digest(token),
        user_id=user.id,
        name=kind + " session",
        kind=kind,
        scopes=[],
        csrf_hash=digest(csrf) if kind == "browser" else None,
        expires_at=now() + timedelta(hours=12 if kind == "browser" else 24),
    )
    db.add(credential)
    db.flush()
    return token, csrf, credential


@dataclass
class Actor:
    db: object
    credential: Credential
    user: User
    workspace: Workspace
    membership: Membership
    scopes: set[str]
    browser: bool

    @property
    def role(self):
        return self.workspace.role

    def need(self, scope, role=None):
        require(
            scope in self.scopes and (role is None or self.role == role),
            "permission_denied",
            f"This {self.role} credential cannot perform {scope}",
            403,
        )

    def human(self):
        require(
            self.browser and self.credential.kind == "browser" and self.membership.approver,
            "human_approval_required",
            "Approval requires an authorized human browser session. Agent and CLI tokens cannot approve.",
            403,
        )


def actor(request: Request, db=Depends(session)):
    authorization = request.headers.get("authorization", "")
    bearer = authorization.startswith("Bearer ")
    token = authorization[7:] if bearer else request.cookies.get("market_session")
    require(bool(token), "unauthenticated", "Sign in or supply a workspace agent token", 401)
    c = db.scalar(select(Credential).where(Credential.token_hash == digest(token)))
    require(
        c is not None and not c.revoked and c.expires_at > now(),
        "unauthenticated",
        "Credential is invalid, expired or revoked",
        401,
    )
    require(
        (c.kind == "browser") != bearer,
        "wrong_credential_transport",
        "Browser sessions use cookies; CLI and agent credentials use Bearer tokens",
        401,
    )
    user = db.get(User, c.user_id)
    require(user is not None and user.active, "unauthenticated", "Account disabled", 401)
    wanted = (
        request.headers.get("x-workspace-id") or c.workspace_id or request.query_params.get("workspace_id")
    )
    if not wanted:
        wanted = db.scalar(
            select(Membership.workspace_id)
            .where(Membership.user_id == user.id)
            .order_by(Membership.workspace_id)
        )
    require(
        c.workspace_id is None or wanted == c.workspace_id,
        "workspace_forbidden",
        "Agent credentials cannot switch to another workspace",
        403,
    )
    member = db.get(Membership, (user.id, wanted))
    require(member is not None, "workspace_forbidden", "You are not a member of this workspace", 403)
    workspace = db.get(Workspace, wanted)
    scopes = set(ROLE_SCOPES[workspace.role])
    if c.kind == "agent":
        scopes &= set(c.scopes)
    elif member.admin:
        scopes |= ADMIN_SCOPES
    browser = not bearer
    if browser and request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        require(
            bool(csrf) and secrets.compare_digest(digest(csrf), c.csrf_hash or ""),
            "csrf_rejected",
            "A valid browser CSRF token is required",
            403,
        )
    return Actor(db, c, user, workspace, member, scopes, browser)


def check_origin(request):
    require(
        request.headers.get("origin") in settings.allowed_origins.split(","),
        "origin_rejected",
        "Browser writes require an allowed Origin",
        403,
    )


def create_agent(a, data):
    a.need("credentials:manage")
    require(
        set(data.scopes) <= ROLE_SCOPES[a.role],
        "invalid_scopes",
        f"Agent scopes must be a subset of the {a.role} permissions: {', '.join(sorted(ROLE_SCOPES[a.role]))}",
        422,
    )
    token = "am_" + secrets.token_urlsafe(40)
    c = Credential(
        token_hash=digest(token),
        user_id=a.user.id,
        workspace_id=a.workspace.id,
        name=data.name,
        kind="agent",
        scopes=sorted(set(data.scopes)),
        expires_at=now() + timedelta(days=data.expires_days),
    )
    a.db.add(c)
    a.db.flush()
    return {
        "id": c.id,
        "name": c.name,
        "token": token,
        "workspace_id": c.workspace_id,
        "role": a.role,
        "scopes": c.scopes,
        "expires_at": c.expires_at.isoformat(),
    }
