import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import func, select

from market import media, storage
from market.auth import ROLE_SCOPES
from market.config import settings
from market.main import app
from market.models import Allocation, Approval, Order, Product

BASE = "/api/v1"
ORIGIN = "http://localhost:3100"


def user(client, key, agent=False):
    response = client.post(
        BASE + "/auth/token", json={"email": key + "@demo.local", "password": settings.demo_password}
    )
    assert response.status_code == 200, response.text
    headers = {"Authorization": "Bearer " + response.json()["token"], "Idempotency-Key": str(uuid4())}
    if agent:
        role = "buyer" if key == "buyer" else "seller"
        result = client.post(
            BASE + "/credentials",
            headers=headers,
            json={"name": "Test agent", "scopes": sorted(ROLE_SCOPES[role])},
        )
        assert result.status_code == 201, result.text
        headers["Authorization"] = "Bearer " + result.json()["token"]
    return headers


def data_request(**overrides):
    return {
        "title": "80 stackable chairs",
        "description": "Secret maximum is 7000 EUR; do not disclose",
        "quantity": 80,
        "material": "polypropylene",
        "stackable": True,
        "destination": "Sofia",
        "latest_delivery": (date.today() + timedelta(days=14)).isoformat(),
        "max_total": 700000,
        "max_deposit_percent": 40,
        "preference": "lower_deposit",
        **overrides,
    }


def launch(client, buyer, **overrides):
    buyer = {**buyer, "Idempotency-Key": str(uuid4())}
    res = client.post(BASE + "/requests", headers=buyer, json=data_request(**overrides))
    assert res.status_code == 201, res.text
    rid = res.json()["id"]
    res = client.post(BASE + f"/requests/{rid}/launch", headers={**buyer, "Idempotency-Key": str(uuid4())})
    assert res.status_code == 200, res.text
    rooms = client.get(BASE + f"/requests/{rid}", headers=buyer).json()["negotiations"]
    return rid, rooms


def action(client, headers, room_id, kind, **values):
    claim = client.post(BASE + f"/negotiations/{room_id}/claim", headers=headers)
    assert claim.status_code == 200, claim.text
    payload = {
        "type": kind,
        "expected_version": claim.json()["expected_version"],
        "claim_token": claim.json()["claim_token"],
        **values,
    }
    return client.post(
        BASE + f"/negotiations/{room_id}/actions",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=payload,
    )


def deal(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    rid, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    result = action(
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
    assert result.status_code == 200, result.text
    result = action(client, buyer, room["id"], "accept", offer_id=result.json()["offer_id"])
    assert result.status_code == 200, result.text
    current = client.get(BASE + f"/negotiations/{room['id']}", headers=buyer).json()
    return buyer, seller, current


def browser(key):
    client = TestClient(app)
    result = client.post(
        BASE + "/auth/session",
        headers={"Origin": ORIGIN},
        json={"email": key + "@demo.local", "password": settings.demo_password},
    )
    assert result.status_code == 200, result.text
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": client.cookies.get("market_csrf")})
    return client


def decision(client, room, reject=False):
    return client.post(
        BASE + f"/approvals/{room['id']}/{'reject' if reject else 'approve'}",
        json={"offer_id": room["current_offer"]["id"], "expected_version": room["version"]},
    )


def test_matching_excludes_stock_and_delivery(client):
    buyer = user(client, "buyer", True)
    rid, rooms = launch(client, buyer)
    assert len(rooms) == 2
    matches = client.get(BASE + f"/requests/{rid}/matches", headers=buyer).json()
    excluded = next(m for m in matches if m["product_id"] == "prod-atelier")
    assert not excluded["eligible"] and len(excluded["reasons"]) == 2


@pytest.mark.parametrize(
    "identity,method,path,payload",
    [
        ("buyer", "PUT", "/products/prod-forma/stock", {"stock": 1, "expected_version": 1}),
        ("forma", "POST", "/requests", data_request()),
        ("buyer", "POST", "/credentials", {"name": "Escalation", "scopes": ["products:write"]}),
        ("forma", "PUT", "/rules", {"expected_version": 1, "rules": {"min_unit_price": 1}}),
    ],
)
def test_agent_roles_cannot_cross_authorization_boundaries(client, identity, method, path, payload):
    response = client.request(method, BASE + path, headers=user(client, identity, True), json=payload)
    assert response.status_code == 403, response.text


def test_owner_cannot_issue_other_role_or_admin_scopes(client):
    for key, scopes in [
        ("buyer", ["products:write"]),
        ("forma", ["requests:write"]),
        ("forma", ["policies:write"]),
        ("buyer", ["credentials:manage"]),
    ]:
        res = client.post(
            BASE + "/credentials",
            headers=user(client, key),
            json={"name": "Bad credential", "scopes": scopes},
        )
        assert res.status_code == 422, res.text


def test_seller_cannot_modify_other_seller_product(client):
    result = client.put(
        BASE + "/products/prod-nord/stock",
        headers=user(client, "forma", True),
        json={"stock": 0, "expected_version": 1},
    )
    assert result.status_code == 404


def test_agent_cannot_switch_workspaces(client):
    headers = user(client, "forma", True)
    headers["X-Workspace-ID"] = "ws-nord"
    assert client.get(BASE + "/auth/me", headers=headers).status_code == 403
    del headers["X-Workspace-ID"]
    assert (
        client.get(BASE + "/auth/me?workspace_id=ws-nord", headers=headers).status_code == 200
    )  # bound token wins


def test_context_privacy_and_room_isolation(client):
    buyer = user(client, "buyer", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    seller = user(client, "forma", True)
    view = client.get(BASE + f"/negotiations/{room['id']}/context", headers=seller)
    assert view.status_code == 200
    ctx = view.json()
    assert "max_total" not in ctx["request"] and "description" not in ctx["request"]
    assert "700000" not in view.text and "7000 EUR" not in view.text
    assert ctx["private_rules"]["min_unit_price"] == 8300
    buyer_context = client.get(BASE + f"/negotiations/{room['id']}/context", headers=buyer).json()
    assert "min_unit_price" not in buyer_context["private_rules"]
    assert (
        client.get(BASE + f"/negotiations/{room['id']}", headers=user(client, "nord", True)).status_code
        == 404
    )
    assert client.get(BASE + "/rules?product_id=prod-forma", headers=buyer).status_code == 403
    assert client.get(BASE + "/requests/" + room["request_id"], headers=seller).status_code == 403


def test_seller_floor_is_enforced_not_prompted(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    response = action(
        client,
        seller,
        room["id"],
        "propose",
        terms={
            "unit_price": 1,
            "quantity": 80,
            "delivery_date": (date.today() + timedelta(days=12)).isoformat(),
            "deposit_percent": 0,
        },
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "policy_violation"


def test_two_browser_approvals_create_one_order_and_allocate_stock(system):
    client, factory = system
    buyer, seller, room = deal(client)
    # Every bearer token, including an owner CLI token, is denied human approval.
    for headers in (buyer, seller, user(client, "buyer")):
        res = client.post(
            BASE + f"/approvals/{room['id']}/approve",
            headers=headers,
            json={"offer_id": room["current_offer"]["id"], "expected_version": room["version"]},
        )
        assert res.status_code == 403
    b, s = browser("buyer"), browser("forma")
    result = decision(b, room)
    assert result.status_code == 200 and result.json()["order"] is None
    result = decision(s, room)
    assert result.status_code == 200, result.text
    assert result.json()["order"]["offer"]["terms"]["total"] == 688000
    assert decision(b, room).status_code == 200
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        assert db.scalar(select(func.count()).select_from(Allocation)) == 1
        assert db.get(Product, "prod-forma").stock == 160
    assert len(client.get(BASE + "/orders", headers=seller).json()["items"]) == 1
    assert client.get(BASE + "/orders", headers=user(client, "nord")).json()["items"] == []


def test_csrf_and_origin_required_for_browser_writes(client):
    buyer, _, room = deal(client)
    b = browser("buyer")
    b.headers.pop("X-CSRF-Token")
    assert decision(b, room).status_code == 403
    b.headers["X-CSRF-Token"] = b.cookies.get("market_csrf")
    b.headers["Origin"] = "https://unrelated.example"
    assert decision(b, room).status_code == 403
    assert (
        client.post(
            BASE + "/auth/session",
            headers={"Origin": "https://unrelated.example"},
            json={"email": "buyer@demo.local", "password": settings.demo_password},
        ).status_code
        == 403
    )


def test_stock_change_invalidates_approval_and_never_revives(system):
    client, factory = system
    _, _, room = deal(client)
    assert decision(browser("buyer"), room).status_code == 200
    seller = user(client, "forma")
    for stock in (79, 240):
        p = client.get(BASE + "/products/prod-forma", headers=seller).json()
        res = client.put(
            BASE + "/products/prod-forma/stock",
            headers=seller,
            json={"stock": stock, "expected_version": p["version"]},
        )
        assert res.status_code == 200, res.text
    assert decision(browser("forma"), room).status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
        assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_policy_change_invalidates_offer(client):
    _, _, room = deal(client)
    seller = user(client, "forma")
    result = client.put(
        BASE + "/rules?product_id=prod-forma",
        headers=seller,
        json={
            "expected_version": 1,
            "rules": {
                "min_unit_price": 9000,
                "min_deposit_percent": 30,
                "max_discount_percent": 20,
                "discount_min_quantity": 50,
            },
        },
    )
    assert result.status_code == 200, result.text
    assert decision(browser("buyer"), room).status_code == 409


def test_idempotency_and_claims(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    first = client.post(BASE + "/requests", headers=buyer, json=data_request())
    second = client.post(BASE + "/requests", headers=buyer, json=data_request())
    assert first.json()["id"] == second.json()["id"]
    changed = client.post(BASE + "/requests", headers=buyer, json=data_request(max_total=500000))
    assert changed.status_code == 409
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    claim = client.post(BASE + f"/negotiations/{room['id']}/claim", headers=seller).json()
    assert client.post(BASE + f"/negotiations/{room['id']}/claim", headers=seller).status_code == 409
    payload = {
        "type": "propose",
        "expected_version": claim["expected_version"],
        "claim_token": claim["claim_token"],
        "terms": {
            "unit_price": 8300,
            "quantity": 80,
            "delivery_date": (date.today() + timedelta(days=12)).isoformat(),
            "deposit_percent": 30,
        },
    }
    first = client.post(BASE + f"/negotiations/{room['id']}/actions", headers=seller, json=payload)
    second = client.post(BASE + f"/negotiations/{room['id']}/actions", headers=seller, json=payload)
    assert first.status_code == 200, first.text
    assert first.json() == second.json()


def test_concurrent_approvals_do_not_double_allocate(system):
    client, factory = system
    _, _, room = deal(client)
    b, s = browser("buyer"), browser("forma")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: decision(c, room), [b, s]))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    with factory() as db:
        assert db.get(Product, "prod-forma").stock == 160
        assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_low_budget_and_impossible_deadline(client):
    buyer = user(client, "buyer", True)
    rid, rooms = launch(client, buyer, latest_delivery=(date.today() + timedelta(days=2)).isoformat())
    assert not rooms
    assert client.get(BASE + "/requests/" + rid, headers=buyer).json()["request"]["state"] == "no_match"
    _, rooms = launch(client, buyer, max_total=400000)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    offered = action(
        client,
        user(client, "forma", True),
        room["id"],
        "propose",
        terms={
            "unit_price": 8300,
            "quantity": 80,
            "delivery_date": (date.today() + timedelta(days=12)).isoformat(),
            "deposit_percent": 30,
        },
    )
    assert offered.status_code == 200
    rejected = action(client, buyer, room["id"], "accept", offer_id=offered.json()["offer_id"])
    assert rejected.status_code == 422


def test_revoked_token_loses_access(client):
    owner = user(client, "buyer")
    issued = client.post(
        BASE + "/credentials", headers=owner, json={"name": "Temporary buyer", "scopes": ["catalogue:read"]}
    ).json()
    h = {"Authorization": "Bearer " + issued["token"]}
    assert client.get(BASE + "/products", headers=h).status_code == 200
    assert client.get(BASE + "/negotiations", headers=h).status_code == 403
    assert client.delete(BASE + "/credentials/" + issued["id"], headers=owner).status_code == 200
    assert client.get(BASE + "/products", headers=h).status_code == 401


def test_images_real_minio_processing_and_access(system, monkeypatch, tmp_path):
    client, factory = system
    bucket = "test-market-" + uuid4().hex
    monkeypatch.setattr(settings, "minio_bucket", bucket)
    monkeypatch.setattr(media, "SessionLocal", factory)
    storage.initialize()
    seller, buyer = user(client, "forma", True), user(client, "buyer", True)
    content = BytesIO()
    PILImage.new("RGB", (120, 100), "green").save(content, "PNG")
    try:
        assert (
            client.post(
                BASE + "/products/prod-forma/images",
                headers=buyer,
                files={"file": ("a.png", content.getvalue(), "image/png")},
            ).status_code
            == 403
        )
        assert (
            client.post(
                BASE + "/products/prod-forma/images",
                headers=user(client, "nord", True),
                files={"file": ("a.png", content.getvalue(), "image/png")},
            ).status_code
            == 403
        )
        result = client.post(
            BASE + "/products/prod-forma/images",
            headers=seller,
            files={"file": ("a.png", content.getvalue(), "image/png")},
        )
        assert result.status_code == 202, result.text
        assert media.process_one()
        items = client.get(BASE + "/products/prod-forma/images", headers=buyer).json()
        assert len(items) == 1 and items[0]["status"] == "ready"
        body = client.get(BASE + items[0]["content_path"], headers=buyer)
        assert body.status_code == 200
        assert hashlib.sha256(body.content).hexdigest() == items[0]["sha256"]
        assert PILImage.open(BytesIO(body.content)).size == (120, 100)
        assert client.get(BASE + items[0]["content_path"]).status_code == 401
        second = client.post(
            BASE + "/products/prod-forma/images",
            headers={**seller, "Idempotency-Key": str(uuid4())},
            files={"file": ("side.png", content.getvalue(), "image/png")},
        )
        assert second.status_code == 202
        assert media.process_one()
        gallery = client.get(BASE + "/products/prod-forma", headers=seller).json()
        ids = [item["id"] for item in gallery["images"]]
        assert len(ids) == 2
        reordered = client.put(
            BASE + "/products/prod-forma/images/order",
            headers=seller,
            json={"image_ids": ids[::-1], "expected_version": gallery["version"]},
        )
        assert reordered.status_code == 200, reordered.text
        assert [item["id"] for item in reordered.json()["images"]] == ids[::-1]
        covered = client.put(
            BASE + "/products/prod-forma/images/cover",
            headers=seller,
            json={"image_id": ids[1], "expected_version": reordered.json()["version"]},
        )
        assert covered.status_code == 200
        assert covered.json()["cover_id"] == ids[1]
        _, _, room = deal(client)
        assert decision(browser("buyer"), room).status_code == 200
        confirmed = decision(browser("forma"), room)
        assert confirmed.status_code == 200
        assert len(confirmed.json()["order"]["offer"]["product"]["images"]) == 2
        gallery = client.get(BASE + "/products/prod-forma", headers=seller).json()
        removed = client.delete(
            BASE + f"/products/prod-forma/images/{ids[0]}",
            headers=seller,
            params={"expected_version": gallery["version"]},
        )
        assert removed.status_code == 200
        # Catalogue removal does not destroy the ordered image evidence.
        assert client.get(BASE + f"/images/{ids[0]}/content", headers=buyer).status_code == 200
        assert (
            client.get(BASE + f"/images/{ids[0]}/content", headers=user(client, "nord", True)).status_code
            == 404
        )
        invalid = client.post(
            BASE + "/products/prod-forma/images",
            headers={**seller, "Idempotency-Key": str(uuid4())},
            files={"file": ("evil.svg", b"<svg>not a raster</svg>", "image/svg+xml")},
        )
        assert invalid.status_code == 422
    finally:
        c = storage.client()
        for obj in c.list_objects(bucket, recursive=True):
            c.remove_object(bucket, obj.object_name)
        c.remove_bucket(bucket)


def test_untrusted_fields_cannot_expand_action_schema(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    room = next(r for r in rooms if r["product_id"] == "prod-forma")
    result = client.post(
        BASE + f"/negotiations/{room['id']}/actions",
        headers=seller,
        json={"type": "approve", "expected_version": 0, "claim_token": "x" * 32, "ignore_policies": True},
    )
    assert result.status_code == 422
