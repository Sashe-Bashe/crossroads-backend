"""Prepare a chair-only negotiation through the real marketplace CLI.

This is an explicit operator command, never an API startup hook. It does not
reset a database or delete storage. Accounts are created only in an empty
database, or recovered using this command's private preparation journal.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from market.auth import passwords
from market.config import settings
from market.db import Base, SessionLocal
from market.models import Membership, User, Workspace
from market.service import mutation

ROOT = Path(__file__).resolve().parents[2]
SKU = "BATTLE-CHAIR-01"
TITLE = "Eight dining chairs / live agent negotiation"
CAPTION = "Original chair photograph supplied by the seller"
AGENT_NAME = "Live chair negotiation"
SCOPES = ["catalogue:read", "images:read", "negotiations:read", "negotiations:act", "orders:read"]
SELLER_RULES = {
    "min_unit_price": 10000,
    "max_discount_percent": 100,
    "min_deposit_percent": 20,
    "discount_min_quantity": 1,
}
BUYER_RULES = {"max_total": 120000, "max_deposit_percent": 30}


class PreparationError(Exception):
    """A safe operator-facing error with no credential-bearing output."""


def private_json(path, value):
    """Atomically replace a private JSON file without a world-readable interval."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".prepare-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def product_document():
    return {
        "name": "Black upholstered dining chair with woven back",
        "description": (
            "Dining chair with black upholstered seat, a woven back panel and a black frame. "
            "The supplied photograph shows the chair in a dining-room setting. Price is per chair; "
            "the table and other pictured items are not included."
        ),
        "sku": SKU,
        "category": "chairs",
        "attributes": {"material": "fabric", "stackable": False, "color": "Black / natural woven back"},
        "unit_price": 14000,
        "delivery_charge": 24000,
        "lead_days": 12,
        "destinations": ["Sofia", "Plovdiv"],
        "stock": 8,
        "published": True,
    }


def request_document(prepared_on):
    return {
        "title": TITLE,
        "description": "Eight dining chairs for Sofia. Negotiate the lowest delivery-inclusive total.",
        "category": "chairs",
        "quantity": 8,
        "material": "fabric",
        "stackable": False,
        "destination": "Sofia",
        "latest_delivery": (date.fromisoformat(prepared_on) + timedelta(days=14)).isoformat(),
        "max_total": BUYER_RULES["max_total"],
        "max_deposit_percent": BUYER_RULES["max_deposit_percent"],
        "preference": "lowest_total",
    }


def initial_state(api_url, image):
    return {
        "version": 1,
        "api_url": api_url,
        "prepared_on": date.today().isoformat(),
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "image_path": str(image),
        "buyer": {"user_id": str(uuid4()), "workspace_id": str(uuid4()), "email": "buyer@demo.local"},
        "seller": {"user_id": str(uuid4()), "workspace_id": str(uuid4()), "email": "forma@demo.local"},
    }


def ensure_accounts(state):
    """Create exactly two humans/workspaces, or verify a previous preparation."""
    if not settings.demo_enabled:
        raise PreparationError("Set DEMO_ENABLED=true explicitly before preparing a demo database")
    if len(settings.demo_password) < 12:
        raise PreparationError("DEMO_PASSWORD must contain at least 12 characters")
    with SessionLocal.begin() as db:
        mutation(db)
        users = list(db.scalars(select(User)))
        workspaces = list(db.scalars(select(Workspace)))
        if not users and not workspaces:
            # An empty users table alone is not sufficient: refuse any orphaned
            # catalogue, history, credential or policy data too.
            for table in Base.metadata.sorted_tables:
                if db.execute(select(table).limit(1)).first() is not None:
                    raise PreparationError("Database is not empty; reset it explicitly before preparation")
            for role, name, company in [
                ("buyer", "Maya Chen", "Forma Studio"),
                ("seller", "Elias Novak", "Forma Supply"),
            ]:
                account = state[role]
                db.add(
                    User(
                        id=account["user_id"],
                        email=account["email"],
                        name=name,
                        password_hash=passwords.hash(settings.demo_password),
                    )
                )
                db.add(Workspace(id=account["workspace_id"], name=company, role=role))
                db.flush()
                db.add(
                    Membership(
                        user_id=account["user_id"],
                        workspace_id=account["workspace_id"],
                        admin=True,
                        approver=True,
                    )
                )
            return
        expected_users = {state[role]["user_id"] for role in ("buyer", "seller")}
        expected_workspaces = {state[role]["workspace_id"] for role in ("buyer", "seller")}
        if {u.id for u in users} != expected_users or {w.id for w in workspaces} != expected_workspaces:
            raise PreparationError("Database is not this preparation's two-account demo; no changes made")
        for role in ("buyer", "seller"):
            account = state[role]
            user = db.get(User, account["user_id"])
            workspace = db.get(Workspace, account["workspace_id"])
            member = db.get(Membership, (user.id, workspace.id))
            if (
                user.email != account["email"]
                or workspace.role != role
                or not user.active
                or member is None
                or not member.admin
                or not member.approver
            ):
                raise PreparationError("Prepared demo account identity or permissions changed")


class CLI:
    """Run the installed source CLI with captured output and private local state."""

    def __init__(self, api_url, config_path, profile, *, token=None, workspace=None, media_root=None):
        self.api_url, self.config_path, self.profile = api_url, Path(config_path), profile
        self.token, self.workspace, self.media_root = token, workspace, media_root

    def call(self, *args, stdin=None, key=None):
        env = dict(os.environ)
        for variable in ("MARKET_TOKEN", "MARKET_WORKSPACE", "MARKET_PROFILE", "MARKET_PASSWORD"):
            env.pop(variable, None)
        env.update(MARKET_API_URL=self.api_url, MARKET_CONFIG=str(self.config_path))
        if self.token:
            env["MARKET_TOKEN"] = self.token
        if self.workspace:
            env["MARKET_WORKSPACE"] = self.workspace
        if self.media_root:
            env["MARKET_MEDIA_ROOT"] = str(self.media_root)
        argv = ["uv", "run", "--project", str(ROOT / "cli"), "market", "--profile", self.profile, *args]
        if key:
            argv += ["--idempotency-key", key]
        try:
            result = subprocess.run(
                [*argv, "--json"], env=env, input=stdin, capture_output=True, text=True, timeout=90
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PreparationError(
                "Marketplace CLI failed to start or timed out; preparation can be resumed"
            ) from exc
        if result.returncode:
            # Do not echo stdout/stderr: credential commands return secrets.
            code = "command_failed"
            try:
                candidate = json.loads(result.stderr).get("error", {}).get("code", "command_failed")
                if isinstance(candidate, str) and candidate.replace("_", "").isalnum():
                    code = candidate
            except (ValueError, AttributeError):
                pass
            raise PreparationError(f"Marketplace CLI {' '.join(args[:2])} failed ({code})")
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise PreparationError("Marketplace CLI returned invalid JSON") from exc

    def document(self, *args, value, key=None):
        return self.call(*args, "--file", "-", stdin=json.dumps(value), key=key)


@contextmanager
def owner_session(cli, email):
    authenticated = False
    try:
        identity = cli.call(
            "auth", "login", "--email", email, "--password-stdin", stdin=settings.demo_password + "\n"
        )
        authenticated = True
        yield identity
    finally:
        # Login stores its token in a temporary 0600 profile. If a later command
        # fails, still revoke the owner session before deleting the profile.
        if authenticated:
            cli.call("auth", "logout")


def ensure_rules(cli, rules):
    current = cli.call("rules", "show")
    if current["rules"] != rules or current["version"] == 0:
        if current["version"] != 0:
            raise PreparationError("Existing demo rules differ; refusing to silently replace them")
        cli.document("rules", "apply", value={"expected_version": 0, "rules": rules})


def only_item(items, predicate, label):
    matches = [item for item in items if predicate(item)]
    if len(items) != len(matches) or len(matches) > 1:
        raise PreparationError(f"Expected only one demo {label}; unrelated or duplicate items exist")
    return matches[0] if matches else None


def prepare_catalogue(buyer, seller, state, save, work_dir, *, image_timeout=180):
    """Resume catalogue/image/request operations without recreating business data."""
    ensure_rules(seller, SELLER_RULES)
    ensure_rules(buyer, BUYER_RULES)
    products = seller.call("products", "list", "--own", "--limit", "100")
    if products["total"] != len(products["items"]):
        raise PreparationError("Expected a chair-only catalogue")
    product = only_item(products["items"], lambda p: p["sku"] == SKU, "product")
    if product is None:
        product = seller.document(
            "products", "create", value=product_document(), key="chair-battle-product-v1"
        )
    elif any(product[key] != value for key, value in product_document().items() if key != "stock"):
        raise PreparationError("Existing chair details changed; preparation will not overwrite them")
    if state.get("product_id") not in (None, product["id"]):
        raise PreparationError("Prepared product ID does not match this database")
    state["product_id"] = product["id"]
    save()
    images = seller.call("products", "images", "list", product["id"])
    image = only_item(images, lambda item: item["caption"] == CAPTION, "product image")
    if image is None:
        uploaded = seller.call(
            "products",
            "images",
            "upload",
            product["id"],
            state["image_path"],
            "--caption",
            CAPTION,
            key="chair-battle-image-v1",
        )
        image = uploaded["images"][0]
    state["image_id"] = image["id"]
    save()
    deadline = time.monotonic() + image_timeout
    while image["status"] != "ready":
        if image["status"] in ("failed", "deleted"):
            raise PreparationError("Chair image processing failed; inspect the image worker before retrying")
        if time.monotonic() >= deadline:
            raise PreparationError("Chair image is still processing; start the worker and rerun preparation")
        time.sleep(min(2, max(0, deadline - time.monotonic())))
        images = seller.call("products", "images", "list", product["id"])
        image = next((item for item in images if item["id"] == state["image_id"]), None)
        if image is None:
            raise PreparationError("Uploaded chair image disappeared")
    fresh = seller.call("products", "show", product["id"])
    if fresh["cover_id"] != image["id"]:
        seller.call("products", "images", "cover", product["id"], image["id"])
    # CLI downloads the processed file and compares its SHA-256 to the API
    # manifest; verification uses the buyer's access, not the seller's access.
    downloaded = buyer.call(
        "products",
        "images",
        "read",
        product["id"],
        "--all",
        "--output-dir",
        str(work_dir / "verified-images"),
    )
    if len(downloaded["images"]) != 1 or downloaded["images"][0]["image_id"] != image["id"]:
        raise PreparationError("Buyer could not read the expected chair photograph")
    state["verified_image_path"] = downloaded["images"][0]["path"]
    save()
    requests = buyer.call("requests", "list", "--limit", "100")
    if requests["total"] != len(requests["items"]):
        raise PreparationError("Expected a single demo purchase request")
    request = only_item(requests["items"], lambda item: item["title"] == TITLE, "purchase request")
    if request is None:
        request = buyer.document(
            "requests", "create", value=request_document(state["prepared_on"]), key="chair-battle-request-v1"
        )
    elif any(request[key] != value for key, value in request_document(state["prepared_on"]).items()):
        raise PreparationError("Existing buyer request changed; preparation will not overwrite it")
    if state.get("request_id") not in (None, request["id"]):
        raise PreparationError("Prepared request ID does not match this database")
    state["request_id"] = request["id"]
    save()
    if request["state"] == "draft":
        buyer.call("requests", "launch", request["id"], key="chair-battle-launch-v1")
    rooms = buyer.call("requests", "show", request["id"])["negotiations"]
    room = only_item(rooms, lambda item: item["product_id"] == product["id"], "negotiation room")
    if room is None:
        raise PreparationError("The chair did not match the request; inspect request matching results")
    state["room_id"] = room["id"]
    save()


def ensure_agent(cli, config, role, config_path):
    scopes = SCOPES + (["requests:write"] if role == "buyer" else [])
    credentials = cli.call("credentials", "list")
    existing = next((item for item in credentials if item["id"] == config[role].get("credential_id")), None)
    if (
        existing
        and not existing["revoked"]
        and datetime.fromisoformat(existing["expires_at"]) > datetime.now(UTC)
        and config[role].get("token")
    ):
        if sorted(existing["scopes"]) != sorted(scopes):
            raise PreparationError("Saved agent permissions differ from the restricted battle scopes")
        return
    # Recover a crash between issuance and saving its one-time secret: revoke
    # only this preparation's named credential before issuing its replacement.
    for credential in credentials:
        if credential["name"] == AGENT_NAME and not credential["revoked"]:
            cli.call("credentials", "revoke", credential["id"])
    issued = cli.document(
        "credentials", "create", value={"name": AGENT_NAME, "scopes": scopes, "expires_days": 7}
    )
    config[role].update(token=issued["token"], credential_id=issued["id"])
    private_json(config_path, config)


def prepare(args):
    image = args.image.expanduser().resolve()
    if not image.is_file():
        raise PreparationError("The original chair photograph does not exist at --image")
    work_dir = args.state_dir.expanduser().resolve()
    state_path, config_path = work_dir / "bootstrap-state.json", work_dir / "config.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else initial_state(args.api_url, image)
    if state.get("version") != 1 or state.get("api_url") != args.api_url:
        raise PreparationError("Preparation journal belongs to a different API or version")
    if state["image_sha256"] != hashlib.sha256(image.read_bytes()).hexdigest():
        raise PreparationError("The original chair photograph changed; use its existing preparation journal")
    state["image_path"] = str(image)
    private_json(state_path, state)
    ensure_accounts(state)
    config = (
        json.loads(config_path.read_text())
        if config_path.exists()
        else {
            "version": 1,
            "api_url": args.api_url,
            "run_dir": str(work_dir / "run"),
            "pause_seconds": 3,
            "timeout_seconds": 900,
            "max_model_calls": 32,
            "mode": "external",
            "buyer": {},
            "seller": {},
        }
    )
    for role in ("buyer", "seller"):
        config[role].update(workspace_id=state[role]["workspace_id"], email=state[role]["email"])
        model = getattr(args, role + "_model") or args.model
        if model:
            config[role]["model"] = model
        config[role].setdefault("model_kwargs", {})
    private_json(config_path, config)
    with tempfile.TemporaryDirectory(prefix="crossroads-prepare-") as scratch, ExitStack() as sessions:
        owners = {}
        for role in ("buyer", "seller"):
            cli = CLI(args.api_url, Path(scratch) / "owners.json", role, media_root=image.parent)
            identity = sessions.enter_context(owner_session(cli, state[role]["email"]))
            if identity["workspace"]["id"] != state[role]["workspace_id"]:
                raise PreparationError("API and operator database do not refer to the same workspace")
            owners[role] = cli
        prepare_catalogue(
            owners["buyer"],
            owners["seller"],
            state,
            lambda: private_json(state_path, state),
            work_dir,
            image_timeout=args.image_timeout,
        )
        for key in ("product_id", "request_id", "room_id"):
            config[key] = state[key]
        config["image_path"] = state["verified_image_path"]
        private_json(config_path, config)
        for role in ("buyer", "seller"):
            ensure_agent(owners[role], config, role, config_path)
    return {
        "status": "prepared",
        "product_id": state["product_id"],
        "request_id": state["request_id"],
        "room_id": state["room_id"],
        "config_path": str(config_path),
        "image_verified": True,
        "human_approval_required": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Explicitly prepare an empty demo database")
    parser.add_argument("--api-url", default=os.environ.get("MARKET_API_URL", "http://127.0.0.1:8100"))
    parser.add_argument("--image", type=Path, default=Path("/home/master/Downloads/stol.jpg"))
    parser.add_argument("--state-dir", type=Path, default=ROOT / "backend" / "work" / "battle")
    parser.add_argument("--model", help="Agent model provider:name; can also be configured before running")
    parser.add_argument("--buyer-model", help="Override the buyer model")
    parser.add_argument("--seller-model", help="Override the seller model")
    parser.add_argument("--image-timeout", type=int, default=180)
    args = parser.parse_args()
    if not args.prepare:
        parser.error("Pass --prepare explicitly; this command does not reset databases or storage")
    if not 1 <= args.image_timeout <= 600:
        parser.error("--image-timeout must be between 1 and 600 seconds")
    args.api_url = args.api_url.rstrip("/")
    try:
        print(json.dumps(prepare(args), indent=2))
    except SQLAlchemyError:
        parser.exit(
            1, "Preparation stopped: database operation failed; verify migrations and connection settings\n"
        )
    except (PreparationError, OSError, ValueError) as exc:
        parser.exit(1, f"Preparation stopped: {exc}\n")


if __name__ == "__main__":
    main()
