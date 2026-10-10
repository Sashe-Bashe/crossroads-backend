from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
import typer
from response_helpers import response_data
from sqlalchemy import func, select
from test_marketplace import BASE, action, browser, deal, decision, launch, user

from market import manage, worker
from market.db import now
from market.models import Approval, Offer, Order, Product, Room, Workspace


def test_competing_requests_cannot_oversell_or_restore_stale_stock(system):
    client, factory = system
    owner = user(client, "forma")
    p = response_data(client.get(BASE + "/products/prod-forma", headers=owner))
    client.put(
        BASE + "/products/prod-forma/stock",
        headers=owner,
        json={"stock": 100, "expected_version": p["version"]},
    ).raise_for_status()
    _, _, first = deal(client)
    _, _, second = deal(client)
    stale_version = response_data(client.get(BASE + "/products/prod-forma", headers=owner))["version"]
    buyer, seller = browser("buyer"), browser("forma")
    assert decision(buyer, first).status_code == 200
    assert decision(buyer, second).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda room: decision(seller, room), [first, second]))
    assert sorted(r.status_code for r in results) == [200, 409]
    with factory() as db:
        assert db.get(Product, "prod-forma").stock == 20
        assert db.scalar(select(func.count()).select_from(Order)) == 1
    result = client.put(
        BASE + "/products/prod-forma/stock",
        headers=owner,
        json={"stock": 100, "expected_version": stale_version},
    )
    assert result.status_code == 409


def test_expiry_removes_approvals_and_user_retry_preserves_offer_history(system, monkeypatch):
    client, factory = system
    buyer, _, room = deal(client)
    assert decision(browser("buyer"), room).status_code == 200
    with factory.begin() as db:
        db.get(Offer, room["current_offer"]["id"]).expires_at = now() - timedelta(seconds=1)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    worker.maintenance()
    assert decision(browser("forma"), room).status_code == 409
    with factory() as db:
        assert db.get(Room, room["id"]).state == "expired"
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
    path = BASE + f"/negotiations/{room['id']}/retry"
    assert client.post(path, headers=buyer).status_code == 403
    response = client.post(path, headers=user(client, "buyer"))
    assert response.status_code == 200, response.text
    assert response_data(response)["run"] == 2
    assert response_data(response)["current_offer"] is None
    assert len(response_data(response)["offers"]) == 1


def test_expired_turn_can_be_reclaimed_but_old_token_is_rejected(system):
    client, factory = system
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    path = BASE + f"/negotiations/{room['id']}"
    old = response_data(client.post(path + "/claim", headers=seller))
    with factory.begin() as db:
        db.get(Room, room["id"]).claim_until = now() - timedelta(seconds=1)
    new = response_data(client.post(path + "/claim", headers=seller))
    payload = {
        "type": "decline",
        "expected_version": old["expected_version"],
        "claim_token": old["claim_token"],
    }
    assert client.post(path + "/actions", headers=seller, json=payload).status_code == 409
    payload["claim_token"] = new["claim_token"]
    response = client.post(
        path + "/actions", headers={**seller, "Idempotency-Key": str(uuid4())}, json=payload
    )
    assert response.status_code == 200, response.text


def test_clarification_before_offer_does_not_advertise_illegal_buyer_proposal(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    assert action(client, seller, room["id"], "request_clarification", topic="images").status_code == 200
    context = response_data(client.get(BASE + f"/negotiations/{room['id']}/context", headers=buyer))
    assert "propose" not in context["allowed_actions"]
    assert action(client, buyer, room["id"], "answer_clarification", topic="images").status_code == 200
    context = response_data(client.get(BASE + f"/negotiations/{room['id']}/context", headers=seller))
    assert "propose" in context["allowed_actions"]


def test_product_rules_cannot_relax_workspace_limits(client):
    seller = user(client, "forma")
    result = client.put(
        BASE + "/rules",
        headers=seller,
        json={
            "expected_version": 1,
            "rules": {
                "min_unit_price": 9000,
                "max_discount_percent": 10,
                "min_deposit_percent": 35,
                "discount_min_quantity": 75,
            },
        },
    )
    assert result.status_code == 200, result.text
    view = response_data(client.get(BASE + "/rules?product_id=prod-forma", headers=seller))
    assert view["effective"]["min_unit_price"] == 9000
    assert view["effective"]["max_discount_percent"] == 10
    assert view["effective"]["min_deposit_percent"] == 35


def test_chunked_body_limit_cannot_be_bypassed(client):
    def chunks():
        for _ in range(12):
            yield b"x" * 1024 * 1024

    response = client.post(
        BASE + "/auth/token", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response_data(response)["error"]["code"] == "body_too_large"


def test_demo_reset_requires_explicit_flag_and_refuses_non_demo_data(system):
    _, factory = system
    with pytest.raises(typer.BadParameter):
        manage.reset_demo(confirm_demo_reset=False, images=False)
    manage.reset_demo(confirm_demo_reset=True, images=False)
    with factory.begin() as db:
        assert db.get(Product, "prod-forma").stock == 240
        db.add(Workspace(id="real-workspace", name="Real company", role="seller"))
    with pytest.raises(typer.BadParameter, match="non-demo"):
        manage.reset_demo(confirm_demo_reset=True, images=False)
    with factory() as db:
        assert db.get(Workspace, "real-workspace") is not None
        assert db.get(Product, "prod-forma") is not None


def test_offer_expiry_is_enforced_before_worker_runs(system):
    from datetime import date

    client, factory = system
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    proposal = action(
        client,
        seller,
        room["id"],
        "propose",
        terms={
            "unit_price": 8300,
            "quantity": 80,
            "delivery_date": (date.today() + timedelta(days=12)).isoformat(),
            "deposit_percent": 30,
        },
    )
    assert proposal.status_code == 200
    path = BASE + f"/negotiations/{room['id']}"
    claim = response_data(client.post(path + "/claim", headers=buyer))
    with factory.begin() as db:
        db.get(Offer, response_data(proposal)["offer_id"]).expires_at = now() - timedelta(seconds=1)
    payload = {
        "type": "accept",
        "offer_id": response_data(proposal)["offer_id"],
        "claim_token": claim["claim_token"],
        "expected_version": claim["expected_version"],
    }
    response = client.post(path + "/actions", headers=buyer, json=payload)
    assert response.status_code == 409
    assert response_data(response)["error"]["code"] == "offer_expired"
    assert response_data(client.get(path + "/context", headers=buyer))["allowed_actions"] == []
    assert client.post(path + "/claim", headers=buyer).status_code == 409
