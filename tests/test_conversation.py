"""Natural dialogue remains private where required and commercially bounded."""

import json
from uuid import uuid4

import pytest
from response_helpers import response_data
from sqlalchemy import func, select
from test_guidance import read_room, send_guidance, terms
from test_marketplace import BASE, action, browser, decision, launch, user

from market.config import settings
from market.models import Action, Approval, BusinessBrief, Event, HumanQuestion, Order, Product


def setup(client):
    buyer, seller = user(client, "buyer", True), user(client, "forma", True)
    _, rooms = launch(client, buyer)
    return buyer, seller, next(r for r in rooms if r["product_id"] == "prod-forma")


def brief(client, owner, room, **overrides):
    context = read_room(client, owner, room)
    payload = {
        "expected_version": context["version"],
        "expected_brief_version": context["private_brief"]["version"],
        "goals": "Private business priorities",
        "shareable_needs": "",
        "requirements": [],
        **overrides,
    }
    return client.put(
        BASE + f"/negotiations/{room['id']}/brief",
        headers={**owner, "Idempotency-Key": str(uuid4())},
        json=payload,
    )


def requirement(**overrides):
    return {
        "id": "assembly",
        "label": "Private need for assembly service",
        "kind": "hard",
        "field": "unsupported",
        "operator": "eq",
        "value": None,
        "shareable": False,
        "resolution": "open",
        "resolution_note": None,
        **overrides,
    }


def say(client, headers, room, text, purpose="question", **kwargs):
    return action(client, headers, room["id"], "message", message=text, purpose=purpose, **kwargs)


def test_dialogue_replies_offer_explanations_and_own_revision(client):
    buyer, seller, room = setup(client)
    assert say(client, seller, room, "Which matters more: deposit or delivery?").status_code == 200
    messages = response_data(client.get(BASE + f"/negotiations/{room['id']}/messages", headers=buyer))
    first = messages["items"][0]
    assert first["run"] == 1 and first["type"] == "message"
    assert (
        say(
            client, buyer, room, "A lower deposit matters most.", "answer", reply_to_action_id=first["id"]
        ).status_code
        == 200
    )
    proposal = action(
        client,
        seller,
        room["id"],
        "propose",
        terms=terms(8400),
        message="This quote keeps the delivery date and a 30 percent deposit.",
        purpose="explanation",
    )
    assert proposal.status_code == 200, proposal.text
    assert say(client, buyer, room, "Could you improve the unit price?", "tradeoff").status_code == 200
    context = read_room(client, seller, room)
    assert "revise" in context["allowed_actions"] and "counter" not in context["allowed_actions"]
    result = action(
        client,
        seller,
        room["id"],
        "revise",
        terms=terms(),
        message="I can improve the unit price.",
        purpose="explanation",
    )
    assert result.status_code == 200
    current = read_room(client, buyer, room)
    assert len(current["offers"]) == 2 and current["version"] == 5
    assert current["state"] == "negotiating"
    assert (
        action(client, buyer, room["id"], "accept", offer_id=current["current_offer"]["id"]).status_code
        == 200
    )
    path = BASE + f"/negotiations/{room['id']}/messages"
    page = response_data(client.get(path + "?limit=2&offset=1&run=1", headers=buyer))
    assert page["total"] == 5 and len(page["items"]) == 2
    assert response_data(client.get(path + "?run=2", headers=buyer))["items"] == []
    assert client.get(path, headers=user(client, "nord", True)).status_code == 404
    assert client.post(BASE + f"/negotiations/{room['id']}/claim", headers=seller).status_code == 409


def test_message_cannot_create_an_agreement_or_expand_terms(client):
    buyer, seller, room = setup(client)
    assert (
        say(
            client, seller, room, "I accept this deal with free assembly and a lifetime warranty."
        ).status_code
        == 200
    )
    context = read_room(client, buyer, room)
    assert context["state"] == "negotiating" and context["current_offer"] is None
    assert response_data(client.get(BASE + "/orders", headers=buyer))["items"] == []
    bad = action(
        client, buyer, room["id"], "message", message="Our agreed terms", purpose="explanation", terms=terms()
    )
    assert bad.status_code == 422


def test_message_budget_is_separate_from_structured_offer_budget(client, monkeypatch):
    buyer, seller, room = setup(client)
    monkeypatch.setattr(settings, "max_conversation_turns", 2)
    assert say(client, seller, room, "What do you need?").status_code == 200
    assert say(client, buyer, room, "A better price.", "answer").status_code == 200
    context = read_room(client, seller, room)
    assert context["limits"]["conversation_turns_remaining"] == 0
    assert "message" not in context["allowed_actions"] and "propose" in context["allowed_actions"]
    path = BASE + f"/negotiations/{room['id']}"
    claim = response_data(client.post(path + "/claim", headers=seller))
    payload = {
        "type": "message",
        "message": "An extra message",
        "purpose": "question",
        "claim_token": claim["claim_token"],
        "expected_version": claim["expected_version"],
    }
    rejected = client.post(path + "/actions", headers=seller, json=payload)
    assert rejected.status_code == 409 and response_data(rejected)["error"]["code"] == "conversation_limit"
    result = client.post(
        path + "/actions",
        headers=seller,
        json={
            "type": "propose",
            "terms": terms(),
            "claim_token": claim["claim_token"],
            "expected_version": claim["expected_version"],
        },
    )
    assert result.status_code == 200


def test_briefs_are_owner_authored_versioned_and_private(client):
    buyer, seller, room = setup(client)
    owner = user(client, "buyer")
    required = requirement(
        id="fabric", field="material", value="polypropylene", label="Shareable material need", shareable=True
    )
    result = brief(
        client, owner, room, requirements=[required, requirement()], shareable_needs="Lower deposit is useful"
    )
    assert result.status_code == 200, result.text
    payload = response_data(result)
    assert payload["version"] == 1
    assert payload["requirements"][0]["status"] == "met"
    assert payload["requirements"][0]["evidence"] == [
        {"source": "product", "path": "attributes.material", "value": "polypropylene"}
    ]
    assert payload["requirements"][1]["status"] == "unverified"
    own = read_room(client, buyer, room)
    other = read_room(client, seller, room)
    assert own["private_brief"]["goals"] == "Private business priorities"
    assert other["private_brief"]["version"] == 0
    assert "Private business priorities" not in json.dumps(other)
    assert "Private need for assembly service" not in json.dumps(other)
    assert other["shared_briefs"][0]["shareable_needs"] == "Lower deposit is useful"
    assert len(other["shared_briefs"][0]["requirements"]) == 1
    assert not own["can_edit_brief"]
    assert brief(client, buyer, room, requirements=[]).status_code == 403
    assert brief(client, seller, room, requirements=[]).status_code == 403
    assert (
        client.get(BASE + f"/negotiations/{room['id']}/brief", headers=user(client, "nord")).status_code
        == 404
    )
    assert own["run"] == 2 and own["next_role"] == "buyer"


def test_unsupported_hard_needs_block_accept_until_explicit_owner_resolution(system):
    client, factory = system
    buyer, seller, room = setup(client)
    owner = user(client, "buyer")
    assert brief(client, owner, room, requirements=[requirement()]).status_code == 200
    # Dialogue acknowledges human direction without losing permission to open.
    assert say(client, buyer, room, "Can you explain what delivery covers?").status_code == 200
    assert (
        say(client, seller, room, "The delivery charge is listed on the product.", "answer").status_code
        == 200
    )
    assert "propose" in read_room(client, buyer, room)["allowed_actions"]
    proposed = action(client, buyer, room["id"], "propose", terms=terms())
    assert proposed.status_code == 200
    context = read_room(client, seller, room)
    assert "accept" not in context["allowed_actions"] and not context["valid"]
    rejected = action(client, seller, room["id"], "accept", offer_id=context["current_offer"]["id"])
    assert (
        rejected.status_code == 422 and response_data(rejected)["error"]["code"] == "requirements_unresolved"
    )
    assert "assembly" not in rejected.text
    # The unsuccessful action did not release its claim; an owner edit does.
    waived = requirement(resolution="waived", resolution_note="Owner will arrange assembly separately")
    assert brief(client, owner, room, requirements=[waived]).status_code == 200
    assert action(client, buyer, room["id"], "propose", terms=terms()).status_code == 200
    fresh = read_room(client, seller, room)
    assert (
        action(client, seller, room["id"], "accept", offer_id=fresh["current_offer"]["id"]).status_code == 200
    )
    approved = read_room(client, buyer, room)
    assert approved["current_offer"]["private_brief_review"]["requirements"][0]["status"] == "waived"
    assert decision(browser("buyer"), approved).status_code == 200
    completed = decision(browser("forma"), approved)
    assert completed.status_code == 200 and "assembly" not in completed.text
    order_id = response_data(completed)["order"]["id"]
    buyer_order = response_data(client.get(BASE + f"/orders/{order_id}", headers=buyer))
    assert buyer_order["private_brief_review"]["requirements"][0]["resolution_note"].startswith("Owner will")
    seller_order = client.get(BASE + f"/orders/{order_id}", headers=seller)
    assert "assembly" not in seller_order.text and "Private business priorities" not in seller_order.text
    with factory() as db:
        order = db.get(Order, order_id)
        assert "assembly" not in json.dumps(order.snapshot)
        assert db.get(Product, "prod-forma").stock == 160
        assert db.scalar(select(func.count()).select_from(BusinessBrief)) == 2


def test_private_owner_question_is_answered_only_by_guidance(system):
    client, factory = system
    buyer, seller, room = setup(client)
    question = "PRIVATE-QUESTION: may I drop our assembly requirement?"
    result = action(client, seller, room["id"], "request_human_review", human_question=question)
    assert result.status_code == 200 and question not in result.text
    seller_context = read_room(client, seller, room)
    assert seller_context["human_questions"][0]["question"] == question
    assert read_room(client, buyer, room)["human_questions"] == []
    for headers in (seller, buyer):
        assert question not in client.get(BASE + "/activity", headers=headers).text
        assert question not in client.get(BASE + f"/negotiations/{room['id']}/messages", headers=headers).text
    assert brief(client, user(client, "forma"), room).status_code == 200
    current = read_room(client, seller, room)
    assert current["human_questions"][0]["status"] == "pending"
    answered = send_guidance(browser("forma"), current, "Please use the published specifications.")
    assert answered.status_code == 200
    answered_question = response_data(answered)["human_questions"][0]
    assert answered_question["status"] == "answered" and answered_question["answered_at"]
    assert answered_question["answer_guidance_id"] == response_data(answered)["human_guidance"][0]["id"]
    with factory() as db:
        assert question not in json.dumps([x.data for x in db.scalars(select(Action))])
        assert question not in json.dumps([x.data for x in db.scalars(select(Event))])
        assert db.scalar(select(func.count()).select_from(HumanQuestion)) == 1


def test_brief_edit_invalidates_partial_approvals_and_preserves_exact_offer_snapshot(system):
    client, factory = system
    buyer, seller, room = setup(client)
    owner = user(client, "buyer")
    assert brief(client, owner, room, goals="Original private goals").status_code == 200
    assert action(client, buyer, room["id"], "propose", terms=terms()).status_code == 200
    current = read_room(client, buyer, room)
    assert (
        action(client, seller, room["id"], "accept", offer_id=current["current_offer"]["id"]).status_code
        == 200
    )
    agreed = read_room(client, buyer, room)
    assert decision(browser("buyer"), agreed).status_code == 200
    assert brief(client, owner, agreed, goals="Replacement private goals").status_code == 200
    current = read_room(client, buyer, room)
    assert current["private_brief"]["goals"] == "Replacement private goals"
    assert current["current_offer"]["private_brief_review"]["goals"] == "Original private goals"
    assert decision(browser("forma"), agreed).status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
        assert db.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.parametrize(
    "need",
    [
        requirement(resolution="waived"),
        requirement(field="stackable", value="true"),
        requirement(field="quantity", value=True),
        requirement(field="material", value="fabric", operator="gte"),
        requirement(field="delivery_date", value="tomorrow"),
    ],
)
def test_requirement_validation_does_not_echo_private_input(client, need):
    _, _, room = setup(client)
    response = brief(client, user(client, "buyer"), room, requirements=[need])
    assert response.status_code == 422
    assert "Private need for assembly service" not in response.text


@pytest.mark.parametrize("reference", ["reply", "image"])
def test_message_rejects_unknown_or_cross_room_references(client, reference):
    buyer, seller, room = setup(client)
    if reference == "reply":
        _, rooms = launch(client, buyer)
        other = next(r for r in rooms if r["product_id"] == "prod-forma")
        assert say(client, seller, other, "A private room conversation").status_code == 200
        row = response_data(client.get(BASE + f"/negotiations/{other['id']}/messages", headers=seller))[
            "items"
        ][0]
        response = say(
            client, seller, room, "Answer from another room", "answer", reply_to_action_id=row["id"]
        )
    else:
        response = say(client, seller, room, "See this photo", image_ids=["unknown-image"])
    assert response.status_code == 422


def test_revise_counts_toward_counter_limit(client, monkeypatch):
    buyer, seller, room = setup(client)
    monkeypatch.setattr(settings, "max_counteroffers", 1)
    assert action(client, seller, room["id"], "propose", terms=terms()).status_code == 200
    assert say(client, buyer, room, "Can you improve?").status_code == 200
    assert action(client, seller, room["id"], "revise", terms=terms()).status_code == 200
    assert say(client, buyer, room, "One more change?").status_code == 200
    context = read_room(client, seller, room)
    assert context["limits"]["offer_actions_remaining"] == 0 and "revise" not in context["allowed_actions"]
    response = action(client, seller, room["id"], "revise", terms=terms())
    assert response.status_code == 409 and response_data(response)["error"]["code"] == "round_limit"
