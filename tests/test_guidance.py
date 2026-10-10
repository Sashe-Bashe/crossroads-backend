"""Real HTTP authorization and state transitions in isolated PostgreSQL schemas."""

import json
import runpy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from response_helpers import response_data
from sqlalchemy import func, select
from test_marketplace import BASE, action, browser, deal, decision, launch, user

from market import worker
from market.auth import digest
from market.db import now
from market.models import (
    Action,
    Approval,
    Event,
    HumanGuidance,
    Membership,
    Offer,
    OfferInvalidation,
    Order,
    Product,
    PurchaseRequest,
    Room,
)
from market.schemas import ActionInput


def send_guidance(browser_client, room, instruction="Try a better price", key=None):
    return browser_client.post(
        BASE + f"/negotiations/{room['id']}/guidance",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={"instruction": instruction, "expected_version": room["version"]},
    )


def read_room(client, headers, room):
    return response_data(client.get(BASE + f"/negotiations/{room['id']}/context", headers=headers))


def terms(price=8300):
    return {
        "unit_price": price,
        "quantity": 80,
        "delivery_date": (date.today() + timedelta(days=12)).isoformat(),
        "deposit_percent": 30,
    }


def test_only_participant_browser_approvers_can_send_guidance(system):
    client, factory = system
    buyer, seller, room = deal(client)
    path = BASE + f"/negotiations/{room['id']}/guidance"
    payload = {"instruction": "Try again", "expected_version": room["version"]}
    for headers in (buyer, seller, user(client, "buyer"), user(client, "forma")):
        assert client.post(path, headers=headers, json=payload).status_code == 403
        assert not read_room(client, headers, room)["can_request_guidance"]
    b = browser("buyer")
    assert response_data(b.get(BASE + f"/negotiations/{room['id']}"))["can_request_guidance"]
    b.headers.pop("X-CSRF-Token")
    assert send_guidance(b, room).status_code == 403
    b.headers["X-CSRF-Token"] = b.cookies.get("market_csrf")
    b.headers["Origin"] = "https://unrelated.example"
    assert send_guidance(b, room).status_code == 403
    assert send_guidance(browser("nord"), room).status_code == 404
    with factory.begin() as db:
        db.get(Membership, ("buyer", "ws-buyer")).approver = False
    b.headers["Origin"] = "http://localhost:3100"
    assert send_guidance(b, room).status_code == 403
    assert not response_data(b.get(BASE + f"/negotiations/{room['id']}"))["can_request_guidance"]


@pytest.mark.parametrize("identity", ["buyer", "forma"])
def test_guidance_privacy_and_old_approvals_invalidated(system, identity):
    client, factory = system
    buyer, seller, room = deal(client)
    b, s = browser("buyer"), browser("forma")
    assert decision(b, room).status_code == 200
    own, other = (buyer, seller) if identity == "buyer" else (seller, buyer)
    role = "buyer" if identity == "buyer" else "seller"
    private = "PRIVATE-TACTIC-91: use our timing advantage, and do not share this note"
    response = send_guidance(b if identity == "buyer" else s, room, "  " + private + "  ")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"negotiation", "status_code"}
    updated = response_data(response)
    assert updated["state"] == "needs_revision" and updated["next_role"] == role
    assert updated["run"] == room["run"] + 1 and updated["version"] == room["version"] + 1
    guidance = updated["human_guidance"][0]
    assert guidance["instruction"] == private and guidance["status"] == "pending"
    assert guidance["role"] == role and guidance["applied_at"] is None
    assert updated["current_offer"]["approvals"] == [] and not updated["valid"]
    notice = updated["activity"][-1]
    assert notice["type"] == "human_requested_negotiation"
    assert notice["data"] == {"role": role, "run": updated["run"]}
    assert private not in json.dumps(updated["activity"])
    assert decision(b, room).status_code == decision(s, room).status_code == 409

    path = BASE + f"/negotiations/{room['id']}"
    for suffix in ("", "/context"):
        assert private in client.get(path + suffix, headers=own).text
        counterparty = client.get(path + suffix, headers=other)
        assert private not in counterparty.text and guidance["id"] not in counterparty.text
        assert response_data(counterparty)["human_guidance"] == []
    for headers in (own, other):
        for endpoint in ("/activity", "/orders", "/agent/inbox"):
            assert private not in client.get(BASE + endpoint, headers=headers).text
    for endpoint in ("/negotiations", f"/approvals/{room['id']}"):
        assert private not in client.get(BASE + endpoint, headers=other).text
    for endpoint in (f"/requests/{room['request_id']}", f"/requests/{room['request_id']}/compare"):
        assert (private in client.get(BASE + endpoint, headers=buyer).text) == (identity == "buyer")
    own_inbox = response_data(client.get(BASE + "/agent/inbox", headers=own))["items"]
    other_inbox = response_data(client.get(BASE + "/agent/inbox", headers=other))["items"]
    assert next(x for x in own_inbox if x["room_id"] == room["id"])["pending_guidance_id"] == guidance["id"]
    assert next(x for x in other_inbox if x["room_id"] == room["id"])["pending_guidance_id"] is None
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.get(PurchaseRequest, room["request_id"]).selected_offer_id is None
        assert db.get(OfferInvalidation, room["current_offer"]["id"]) is not None
        assert private not in json.dumps([e.data for e in db.scalars(select(Event))])
        assert private not in json.dumps([x.data for x in db.scalars(select(Action))])
        assert private not in json.dumps([x.terms for x in db.scalars(select(Offer))])


def test_new_guidance_supersedes_pending_claim_and_requires_exact_ack(system):
    client, factory = system
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    path = BASE + f"/negotiations/{room['id']}"
    old_claim = response_data(client.post(path + "/claim", headers=seller))
    b = browser("buyer")
    first = response_data(send_guidance(b, room, "First private instruction"))
    stale = client.post(
        path + "/actions",
        headers=seller,
        json={
            "type": "propose",
            "terms": terms(),
            "expected_version": old_claim["expected_version"],
            "claim_token": old_claim["claim_token"],
        },
    )
    assert stale.status_code == 409 and response_data(stale)["error"]["code"] == "stale_version"
    buyer_claim = response_data(client.post(path + "/claim", headers=buyer))
    second = response_data(send_guidance(b, first, "Updated private instruction"))
    assert [g["status"] for g in second["human_guidance"]] == ["pending", "superseded"]
    latest = second["human_guidance"][0]["id"]
    current = response_data(client.post(path + "/claim", headers=buyer))
    assert current["claim_token"] != buyer_claim["claim_token"]
    payload = {
        "type": "propose",
        "terms": terms(),
        "expected_version": current["expected_version"],
        "claim_token": current["claim_token"],
    }
    for guidance_id in (None, first["human_guidance"][0]["id"], "unknown-id"):
        rejected = client.post(path + "/actions", headers=buyer, json={**payload, "guidance_id": guidance_id})
        assert rejected.status_code == 409 and response_data(rejected)["error"]["code"] == "guidance_required"
    payload["guidance_id"] = latest
    rejected = client.post(path + "/actions", headers=buyer, json={**payload, "terms": terms(9000)})
    assert rejected.status_code == 422 and response_data(rejected)["error"]["code"] == "policy_violation"
    assert read_room(client, buyer, room)["human_guidance"][0]["status"] == "pending"
    accepted = client.post(path + "/actions", headers=buyer, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert client.post(path + "/actions", headers=buyer, json=payload).json() == accepted.json()
    state = read_room(client, buyer, room)
    assert state["current_offer"]["sender"] == "buyer"
    assert state["human_guidance"][0]["status"] == "applied"
    assert state["human_guidance"][0]["applied_at"] is not None
    assert action(client, seller, room["id"], "counter", terms=terms()).status_code == 200
    rejected = action(client, buyer, room["id"], "counter", terms=terms(), guidance_id=latest)
    assert rejected.status_code == 409 and response_data(rejected)["error"]["code"] == "stale_guidance"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(HumanGuidance)) == 2


@pytest.mark.parametrize("identity", ["buyer", "forma"])
def test_each_role_can_revise_after_guidance_with_original_rules(system, identity):
    client, factory = system
    buyer, seller, room = deal(client)
    agent = buyer if identity == "buyer" else seller
    guided = response_data(send_guidance(browser(identity), room))
    context = read_room(client, agent, room)
    assert "propose" in context["allowed_actions"]
    assert context["limits"]["counteroffers_per_side"] == 3
    private = context["private_rules"]
    assert private["max_total" if identity == "buyer" else "min_unit_price"] == (
        700000 if identity == "buyer" else 8300
    )
    path = BASE + f"/negotiations/{room['id']}"
    claim = response_data(client.post(path + "/claim", headers=agent))
    payload = {
        "type": "propose",
        "expected_version": claim["expected_version"],
        "claim_token": claim["claim_token"],
        "guidance_id": guided["human_guidance"][0]["id"],
        "terms": terms(9000 if identity == "buyer" else 8299),
    }
    assert client.post(path + "/actions", headers=agent, json=payload).status_code == 422
    payload["terms"] = terms(8300 if identity == "buyer" else 8400)
    response = client.post(path + "/actions", headers=agent, json=payload)
    assert response.status_code == 200, response.text
    fresh = read_room(client, agent, room)
    assert len(fresh["offers"]) == 2 and fresh["current_offer"]["version"] == 2
    opposite = seller if identity == "buyer" else buyer
    assert (
        action(client, opposite, room["id"], "accept", offer_id=fresh["current_offer"]["id"]).status_code
        == 200
    )
    final = read_room(client, agent, room)
    assert final["state"] == "awaiting_approval" and final["current_offer"]["approvals"] == []
    assert decision(browser("buyer"), final).status_code == 200
    result = decision(browser("forma"), final)
    assert result.status_code == 200 and response_data(result)["state"] == "ordered"
    assert "Try a better price" not in result.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.get(Product, "prod-forma").stock == 160


@pytest.mark.parametrize("state", ["expired", "declined", "needs_human_review", "needs_revision"])
def test_guidance_reopens_terminal_attempt_and_ignores_old_offer_expiry(system, monkeypatch, state):
    client, factory = system
    buyer, _, room = deal(client)
    with factory.begin() as db:
        current = db.get(Room, room["id"])
        current.state = state
        current.expires_at = now() - timedelta(seconds=1)
        db.get(Offer, room["current_offer"]["id"]).expires_at = now() - timedelta(seconds=1)
    response = send_guidance(browser("buyer"), room)
    assert response.status_code == 200, response.text
    guided = response_data(response)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    worker.maintenance()
    context = read_room(client, buyer, room)
    assert context["state"] == "needs_revision" and "propose" in context["allowed_actions"]
    result = action(
        client, buyer, room["id"], "propose", terms=terms(), guidance_id=guided["human_guidance"][0]["id"]
    )
    assert result.status_code == 200, result.text
    assert read_room(client, buyer, room)["valid"]


def test_guidance_idempotency_validation_and_stale_versions(system):
    client, factory = system
    _, _, room = deal(client)
    b = browser("buyer")
    path = BASE + f"/negotiations/{room['id']}/guidance"
    for text in ("", "   ", "x" * 2001):
        assert send_guidance(b, room, text).status_code == 422
    assert b.post(path, json={"instruction": "Try", "expected_version": room["version"]}).status_code == 422
    key = str(uuid4())
    result = send_guidance(b, room, "Try price", key)
    assert result.status_code == 200
    assert send_guidance(b, room, "Try price", key).json() == result.json()
    assert send_guidance(b, room, "Changed text", key).status_code == 409
    stale = send_guidance(b, room, "New key with stale version")
    assert stale.status_code == 409 and response_data(stale)["error"]["code"] == "stale_version"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(HumanGuidance)) == 1
        assert db.get(Room, room["id"]).run == room["run"] + 1
        # An owner with memberships on both sides must not replay a cached private
        # response across workspaces, even though the credential is unchanged.
    with factory.begin() as db:
        db.add(Membership(user_id="buyer", workspace_id="ws-forma", admin=True, approver=True))
    crossed = b.post(
        path,
        headers={"Idempotency-Key": key, "X-Workspace-ID": "ws-forma"},
        json={"instruction": "Try price", "expected_version": room["version"]},
    )
    assert crossed.status_code == 409 and "Try price" not in crossed.text


@pytest.mark.parametrize("blocked", ["ordered", "cancelled", "out_of_stock", "unpublished", "deadline"])
def test_guidance_never_reopens_unavailable_or_confirmed_commerce(system, blocked):
    client, factory = system
    buyer, _, room = deal(client)
    if blocked == "ordered":
        assert decision(browser("buyer"), room).status_code == 200
        assert decision(browser("forma"), room).status_code == 200
    elif blocked == "cancelled":
        assert client.post(BASE + f"/requests/{room['request_id']}/cancel", headers=buyer).status_code == 200
    else:
        with factory.begin() as db:
            product = db.get(Product, "prod-forma")
            if blocked == "out_of_stock":
                product.stock = 0
            elif blocked == "unpublished":
                product.published = False
            else:
                request = db.get(PurchaseRequest, room["request_id"])
                request.data = {**request.data, "latest_delivery": date.today().isoformat()}
    b = browser("buyer")
    current = response_data(b.get(BASE + f"/negotiations/{room['id']}"))
    assert not current["can_request_guidance"]
    response = send_guidance(b, current)
    assert response.status_code == 409 and response_data(response)["error"]["code"] == "invalid_state"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(HumanGuidance)) == 0


def test_pre_upgrade_action_fingerprint_remains_replayable(system):
    client, factory = system
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    path = BASE + f"/negotiations/{room['id']}"
    claim = response_data(client.post(path + "/claim", headers=seller))
    payload = {
        "type": "propose",
        "expected_version": claim["expected_version"],
        "claim_token": claim["claim_token"],
        "terms": terms(),
    }
    response = client.post(path + "/actions", headers=seller, json=payload)
    assert response.status_code == 200
    legacy = ActionInput.model_validate(payload).model_dump(mode="json")
    for field in ("guidance_id", "message", "purpose", "reply_to_action_id", "image_ids", "human_question"):
        del legacy[field]
    with factory() as db:
        stored = db.scalar(select(Action).where(Action.room_id == room["id"]))
        assert stored.request_hash == digest(json.dumps(legacy, sort_keys=True))
    assert client.post(path + "/actions", headers=seller, json=payload).json() == response.json()


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_guidance_creates_only_one_attempt(system, same_key):
    client, factory = system
    _, _, room = deal(client)
    b = browser("buyer")
    key = str(uuid4())
    keys = [key, key if same_key else str(uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda k: send_guidance(b, room, key=k), keys))
    assert sorted(r.status_code for r in results) == ([200, 200] if same_key else [200, 409])
    if same_key:
        assert results[0].json() == results[1].json()
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(HumanGuidance)) == 1
        assert db.get(Room, room["id"]).run == room["run"] + 1


def test_each_partys_pending_guidance_stays_private_and_independent(client):
    buyer, seller, room = deal(client)
    first = response_data(send_guidance(browser("buyer"), room, "Private buyer tactic"))
    second = response_data(send_guidance(browser("forma"), first, "Private seller tactic"))
    assert second["next_role"] == "seller"
    assert len(second["human_guidance"]) == 1
    seller_id = second["human_guidance"][0]["id"]
    own = read_room(client, buyer, room)
    assert len(own["human_guidance"]) == 1
    assert own["human_guidance"][0]["status"] == "pending"
    buyer_id = own["human_guidance"][0]["id"]
    assert seller_id != buyer_id
    assert (
        action(client, seller, room["id"], "propose", terms=terms(), guidance_id=seller_id).status_code == 200
    )
    assert (
        action(client, buyer, room["id"], "counter", terms=terms(), guidance_id=buyer_id).status_code == 200
    )
    for headers in (buyer, seller):
        assert read_room(client, headers, room)["human_guidance"][0]["status"] == "applied"


def test_buyer_can_revise_own_last_offer_and_each_run_resets_counter_budget(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    assert action(client, seller, room["id"], "propose", terms=terms()).status_code == 200
    for _ in range(3):
        assert action(client, buyer, room["id"], "counter", terms=terms()).status_code == 200
        assert action(client, seller, room["id"], "counter", terms=terms()).status_code == 200
    assert "counter" not in read_room(client, buyer, room)["allowed_actions"]
    current = read_room(client, buyer, room)
    b = browser("buyer")
    guided = response_data(send_guidance(b, current))
    assert (
        action(
            client, buyer, room["id"], "propose", terms=terms(), guidance_id=guided["human_guidance"][0]["id"]
        ).status_code
        == 200
    )
    buyer_offer = read_room(client, buyer, room)
    # The seller used all three counters in the old run, but has a fresh budget.
    assert "counter" in read_room(client, seller, room)["allowed_actions"]
    assert (
        action(client, seller, room["id"], "accept", offer_id=buyer_offer["current_offer"]["id"]).status_code
        == 200
    )
    current = read_room(client, buyer, room)
    guided = response_data(send_guidance(b, current))
    assert current["current_offer"]["sender"] == "buyer"
    assert "propose" in read_room(client, buyer, room)["allowed_actions"]
    assert (
        action(
            client, buyer, room["id"], "propose", terms=terms(), guidance_id=guided["human_guidance"][0]["id"]
        ).status_code
        == 200
    )
    assert action(client, seller, room["id"], "counter", terms=terms()).status_code == 200


def test_migration_adds_guidance_to_existing_commerce_without_reset(system):
    client, factory = system
    buyer, _, room = deal(client)
    migration = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "alembic/versions/8f2b7c19a4d0_private_human_guidance.py")
    )
    conversation_migration = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "alembic/versions/4d9a80cb6f21_conversation_and_business_needs.py"
        )
    )
    # The fixture's schema translation keeps both migration directions isolated
    # from the running demo, just like every HTTP integration test.
    with factory.begin() as db:
        connection = db.connection()
        schema = connection.get_execution_options()["schema_translate_map"][None]
        assert schema.startswith("test_") and schema.replace("_", "").isalnum()
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        with Operations.context(MigrationContext.configure(connection)):
            conversation_migration["downgrade"]()
            migration["downgrade"]()
            migration["upgrade"]()
            conversation_migration["upgrade"]()
    fresh = read_room(client, buyer, room)
    assert fresh["current_offer"]["id"] == room["current_offer"]["id"]
    assert fresh["state"] == "awaiting_approval" and fresh["human_guidance"] == []
    assert send_guidance(browser("buyer"), fresh).status_code == 200
    with factory() as db:
        assert db.get(Product, "prod-forma").stock == 240
        assert db.scalar(select(func.count()).select_from(HumanGuidance)) == 1
