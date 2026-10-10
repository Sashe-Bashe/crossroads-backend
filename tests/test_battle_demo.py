"""Preparation safety and bargaining limits; never touch the running demo data."""

import importlib.util
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from market.db import Base
from market.models import Membership, Policy, Product, User, Workspace
from market.policies import check_terms

SPEC = importlib.util.spec_from_file_location(
    "battle_demo", Path(__file__).resolve().parents[1] / "scripts" / "battle_demo.py"
)
battle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(battle)


@pytest.fixture
def isolated_accounts(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(battle, "SessionLocal", factory)
    monkeypatch.setattr(battle, "mutation", lambda db: None)
    monkeypatch.setattr(battle.settings, "demo_enabled", True)
    image = tmp_path / "chair.jpg"
    image.write_bytes(b"the uploaded photograph")
    state = battle.initial_state("http://127.0.0.1:8100", image)
    yield factory, state
    engine.dispose()


def test_accounts_are_empty_database_only_and_reentry_preserves_identity(isolated_accounts):
    factory, state = isolated_accounts
    battle.ensure_accounts(state)
    with factory() as db:
        hashes = {user.id: user.password_hash for user in db.scalars(select(User))}
        assert len(hashes) == 2
        assert set(db.scalars(select(Workspace.role))) == {"buyer", "seller"}
        assert all(member.admin and member.approver for member in db.scalars(select(Membership)))
        assert list(db.scalars(select(Product))) == []
        assert list(db.scalars(select(Policy))) == []
    battle.ensure_accounts(state)
    with factory() as db:
        assert {user.id: user.password_hash for user in db.scalars(select(User))} == hashes
    with factory.begin() as db:
        db.add(User(email="unrelated@example.test", name="Unrelated", password_hash="test"))
    with pytest.raises(battle.PreparationError, match="not this preparation"):
        battle.ensure_accounts(state)
    with factory() as db:
        assert len(list(db.scalars(select(User)))) == 3


def test_prepare_refuses_unrelated_orphan_data_and_requires_demo_flag(isolated_accounts, monkeypatch):
    factory, state = isolated_accounts
    monkeypatch.setattr(battle.settings, "demo_enabled", False)
    with pytest.raises(battle.PreparationError, match="DEMO_ENABLED"):
        battle.ensure_accounts(state)
    monkeypatch.setattr(battle.settings, "demo_enabled", True)
    # SQLite deliberately allows this orphan in the isolated test schema. The
    # precondition must inspect all tables instead of checking users only.
    with factory.begin() as db:
        db.add(Policy(workspace_id="orphan", target="workspace", version=1, data={}))
    with pytest.raises(battle.PreparationError, match="not empty"):
        battle.ensure_accounts(state)
    with factory() as db:
        assert list(db.scalars(select(User))) == []


def test_private_configuration_is_atomically_replaced_with_restricted_permissions(tmp_path):
    path = tmp_path / "private" / "config.json"
    battle.private_json(path, {"token": "private-value"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    os.chmod(path, 0o644)
    battle.private_json(path, {"token": "replacement"})
    assert json.loads(path.read_text()) == {"token": "replacement"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.glob(".prepare-*")) == []


def test_cli_secrets_stay_out_of_arguments_and_failure_output(monkeypatch, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=1, stdout='{"token":"secret-agent-token"}', stderr="secret-password"
        )

    monkeypatch.setattr(battle.subprocess, "run", run)
    monkeypatch.setenv("MARKET_TOKEN", "unrelated-inherited-owner-token")
    cli = battle.CLI("http://127.0.0.1:8100", tmp_path / "config.json", "seller", token="agent-token")
    with pytest.raises(battle.PreparationError) as error:
        cli.call("auth", "login", "--password-stdin", stdin="secret-password\n")
    argv, options = calls[0]
    assert "secret" not in str(error.value)
    assert "secret-password" not in " ".join(argv) and "agent-token" not in " ".join(argv)
    assert options["env"]["MARKET_TOKEN"] == "agent-token"
    assert options["input"] == "secret-password\n"


def test_owner_session_is_revoked_when_later_preparation_fails():
    calls = []

    class Owner:
        def call(self, *args, **kwargs):
            calls.append(args)
            return {"workspace": {"id": "seller"}}

    with pytest.raises(RuntimeError, match="later failure"):
        with battle.owner_session(Owner(), "forma@demo.local"):
            raise RuntimeError("later failure")
    assert calls[-1] == ("auth", "logout")


class MockMarketplace:
    """A small stateful CLI boundary; no direct API/DB shortcut in preparation."""

    def __init__(self):
        self.products, self.images, self.requests, self.rooms = [], [], [], []
        self.rules = {"buyer": {"version": 0, "rules": {}}, "seller": {"version": 0, "rules": {}}}
        self.writes, self.reads = [], []

    def cli(self, role):
        market = self

        class Client:
            def document(self, *args, value, key=None):
                market.writes.append((role, args, value))
                if args == ("rules", "apply"):
                    market.rules[role] = {"version": 1, "rules": value["rules"]}
                    return market.rules[role]
                if args == ("products", "create"):
                    assert role == "seller"
                    item = {"id": "chair", "cover_id": None, **value}
                    market.products.append(item)
                    return item
                if args == ("requests", "create"):
                    assert role == "buyer"
                    item = {"id": "request", "state": "draft", **value}
                    market.requests.append(item)
                    return item
                raise AssertionError(args)

            def call(self, *args, **kwargs):
                market.reads.append((role, args))
                match args[:3]:
                    case ("rules", "show"):
                        return market.rules[role]
                    case ("products", "list", _):
                        return {"items": market.products, "total": len(market.products)}
                    case ("products", "show", _):
                        return market.products[0]
                    case ("products", "images", "list"):
                        return market.images
                    case ("products", "images", "upload"):
                        market.writes.append((role, args, None))
                        market.images.append({"id": "image", "status": "ready", "caption": battle.CAPTION})
                        return {"images": market.images}
                    case ("products", "images", "cover"):
                        market.writes.append((role, args, None))
                        market.products[0]["cover_id"] = "image"
                        return market.products[0]
                    case ("products", "images", "read"):
                        assert role == "buyer"
                        return {"images": [{"image_id": "image", "path": "/tmp/verified-image.jpg"}]}
                    case ("requests", "list", _):
                        return {"items": market.requests, "total": len(market.requests)}
                    case ("requests", "launch", _):
                        market.writes.append((role, args, None))
                        market.requests[0]["state"] = "negotiating"
                        market.rooms.append({"id": "room", "product_id": "chair"})
                        return market.requests[0]
                    case ("requests", "show", _):
                        return {"request": market.requests[0], "negotiations": market.rooms}
                raise AssertionError(args)

        return Client()


def test_catalogue_prepare_and_retry_create_one_product_image_request_and_room(tmp_path):
    market = MockMarketplace()
    state = {"prepared_on": date.today().isoformat(), "image_path": "/tmp/chair.jpg"}
    checkpoints = []

    def save():
        checkpoints.append(dict(state))

    buyer, seller = market.cli("buyer"), market.cli("seller")
    battle.prepare_catalogue(buyer, seller, state, save, tmp_path)
    writes = list(market.writes)
    assert state["room_id"] == "room" and state["verified_image_path"].endswith(".jpg")
    assert market.products[0]["unit_price"] == 14000 and market.products[0]["stock"] == 8
    assert market.rules["seller"]["rules"]["min_unit_price"] == 10000
    assert len(market.products) == len(market.images) == len(market.requests) == len(market.rooms) == 1
    assert any(checkpoint.get("product_id") and "request_id" not in checkpoint for checkpoint in checkpoints)
    battle.prepare_catalogue(buyer, seller, state, save, tmp_path)
    assert market.writes == writes


def test_failed_image_never_launches_a_request_and_existing_data_is_not_overwritten(tmp_path):
    market = MockMarketplace()
    state = {"prepared_on": date.today().isoformat(), "image_path": "/tmp/chair.jpg"}
    market.images = [{"id": "image", "status": "failed", "caption": battle.CAPTION}]
    with pytest.raises(battle.PreparationError, match="processing failed"):
        battle.prepare_catalogue(market.cli("buyer"), market.cli("seller"), state, lambda: None, tmp_path)
    assert market.requests == []
    market.products[0]["unit_price"] = 20000
    with pytest.raises(battle.PreparationError, match="will not overwrite"):
        battle.prepare_catalogue(market.cli("buyer"), market.cli("seller"), state, lambda: None, tmp_path)
    assert market.products[0]["unit_price"] == 20000


def test_demo_price_floor_is_private_and_buyer_total_includes_delivery(isolated_accounts):
    factory, state = isolated_accounts
    battle.ensure_accounts(state)
    with factory.begin() as db:
        for role, rules in (("buyer", battle.BUYER_RULES), ("seller", battle.SELLER_RULES)):
            db.add(
                Policy(workspace_id=state[role]["workspace_id"], target="workspace", version=1, data=rules)
            )
        db.flush()
        document = battle.product_document()
        product = SimpleNamespace(
            id="chair", workspace_id=state["seller"]["workspace_id"], data=document, stock=8, published=True
        )
        request = SimpleNamespace(
            workspace_id=state["buyer"]["workspace_id"],
            data=battle.request_document(date.today().isoformat()),
        )
        terms = {
            "unit_price": 9900,
            "quantity": 8,
            "delivery_date": (date.today() + timedelta(days=12)).isoformat(),
            "deposit_percent": 25,
        }
        assert check_terms(db, product, request, terms, "buyer") == []
        assert check_terms(db, product, request, terms, "seller") == [
            "Supplier commercial requirements are not satisfied"
        ]
        assert check_terms(db, product, request, {**terms, "unit_price": 10000}, "both") == []
        assert check_terms(db, product, request, {**terms, "unit_price": 12000}, "both") == []
        assert check_terms(db, product, request, {**terms, "unit_price": 12001}, "buyer") == [
            "Buyer budget requirements are not satisfied"
        ]
        # Public asking price is deliberately above the seller's private floor.
        assert document["unit_price"] == 14000 and "min_unit_price" not in document


@pytest.mark.parametrize("role", ["buyer", "seller"])
def test_agent_credentials_are_role_restricted_and_reused_without_new_secrets(tmp_path, role):
    created = []
    scopes = battle.SCOPES + (["requests:write"] if role == "buyer" else [])
    expires_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    credentials = []

    class Owner:
        def call(self, *args):
            assert args == ("credentials", "list")
            return credentials

        def document(self, *args, value):
            assert args == ("credentials", "create")
            created.append(value)
            credentials.append(
                {
                    "id": "limited",
                    "name": battle.AGENT_NAME,
                    "scopes": value["scopes"],
                    "revoked": False,
                    "expires_at": expires_at,
                }
            )
            return {"id": "limited", "token": "only-the-runtime-receives-this"}

    config = {role: {"workspace_id": role}}
    path = tmp_path / "config.json"
    battle.ensure_agent(Owner(), config, role, path)
    battle.ensure_agent(Owner(), config, role, path)
    assert len(created) == 1
    assert created[0]["scopes"] == scopes
    assert not {"products:write", "images:write", "policies:write", "credentials:manage"} & set(scopes)
    assert ("requests:write" in scopes) == (role == "buyer")
    assert json.loads(path.read_text())[role]["token"] == "only-the-runtime-receives-this"


def test_expired_agent_credential_is_revoked_before_replacement(tmp_path):
    calls = []
    expired = {
        "id": "expired",
        "name": battle.AGENT_NAME,
        "scopes": battle.SCOPES,
        "revoked": False,
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }

    class Owner:
        def call(self, *args):
            calls.append(args)
            return [expired] if args == ("credentials", "list") else {"revoked": True}

        def document(self, *args, value):
            calls.append(args)
            return {"id": "replacement", "token": "replacement-secret"}

    config = {"seller": {"credential_id": "expired", "token": "old-secret"}}
    battle.ensure_agent(Owner(), config, "seller", tmp_path / "config.json")
    assert calls[1:] == [("credentials", "revoke", "expired"), ("credentials", "create")]
    assert config["seller"]["token"] == "replacement-secret"
