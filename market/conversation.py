"""Participant dialogue and owner-authored needs. Free text never creates terms."""

from sqlalchemy import select

from .db import now
from .errors import require
from .models import BusinessBrief, HumanQuestion


def latest_brief(db, room_id, workspace_id):
    return db.scalar(
        select(BusinessBrief)
        .where(BusinessBrief.room_id == room_id, BusinessBrief.workspace_id == workspace_id)
        .order_by(BusinessBrief.version.desc())
    )


def evaluate_brief(brief, product, request, terms=None):
    data = brief.data if brief else {"goals": "", "shareable_needs": "", "requirements": []}
    product_data = product.data if hasattr(product, "data") else product
    request_data = request.data if hasattr(request, "data") else request
    facts = {
        "material": ("product", "attributes.material", product_data["attributes"]["material"]),
        "stackable": ("product", "attributes.stackable", product_data["attributes"]["stackable"]),
        "destination": ("request", "destination", request_data["destination"]),
        "quantity": ("offer" if terms else "request", "quantity", (terms or request_data)["quantity"]),
    }
    for field in ("unit_price", "total", "deposit_percent", "delivery_date"):
        if terms is not None:
            facts[field] = ("offer", field, terms[field])
    evaluated = []
    for item in data["requirements"]:
        evidence = []
        if item["resolution"] == "waived":
            status = "waived"
        elif item["field"] not in facts:
            status = "unverified"
        else:
            source, path, actual = facts[item["field"]]
            expected = item["value"]
            left, right = (
                (actual.casefold(), expected.casefold()) if isinstance(actual, str) else (actual, expected)
            )
            met = (
                left == right
                if item["operator"] == "eq"
                else left <= right
                if item["operator"] == "lte"
                else left >= right
            )
            status = "met" if met else "unmet"
            evidence = [{"source": source, "path": path, "value": actual}]
        evaluated.append(
            {
                **item,
                "status": status,
                "evidence": evidence,
                "blocks_agreement": item["kind"] == "hard" and status in ("unmet", "unverified"),
            }
        )
    return {
        "version": brief.version if brief else 0,
        "goals": data["goals"],
        "shareable_needs": data["shareable_needs"],
        "requirements": evaluated,
    }


def brief_views(db, room, request, product, terms=None):
    return {
        role: evaluate_brief(latest_brief(db, room.id, workspace), product, request, terms)
        for role, workspace in (("buyer", request.workspace_id), ("seller", room.seller_id))
    }


def shared_briefs(views):
    return [
        {
            "role": role,
            "version": view["version"],
            "shareable_needs": view["shareable_needs"],
            "requirements": [
                {k: v for k, v in item.items() if k != "resolution_note"}
                for item in view["requirements"]
                if item["shareable"]
            ],
        }
        for role, view in views.items()
    ]


def blocked(views):
    return any(item["blocks_agreement"] for view in views.values() for item in view["requirements"])


def can_edit(a):
    return a.credential.kind in ("browser", "cli") and a.membership.admin


def write_brief(a, room_id, data):
    from . import service as s

    require(can_edit(a), "owner_required", "Only a workspace owner user can edit business needs", 403)
    room, request, product = s.room_for(a, room_id)
    require(
        room.version == data.expected_version,
        "stale_version",
        "Refresh the room before editing its brief",
        409,
    )
    previous = latest_brief(a.db, room.id, a.workspace.id)
    require(
        (previous.version if previous else 0) == data.expected_brief_version,
        "stale_brief",
        "Refresh your private business brief before editing",
        409,
    )
    require(
        s.guidance_available(room, request, product),
        "invalid_state",
        "This room cannot restart with the current request and product availability",
        409,
    )
    brief = BusinessBrief(
        room_id=room.id,
        workspace_id=a.workspace.id,
        user_id=a.user.id,
        version=data.expected_brief_version + 1,
        data=data.model_dump(mode="json", exclude={"expected_version", "expected_brief_version"}),
    )
    a.db.add(brief)
    # Reopening is authorized by this owner operation, not by an agent's prose.
    s.restart_from_human(a, room, request, "Business needs changed; obtain a new offer and both approvals")
    s.room_event(a.db, room, a.user.id, "business_brief_updated", {"role": a.role, "run": room.run})
    s.refresh_request(a.db, request)
    return evaluate_brief(brief, product, request)


def questions(a, room):
    return [
        {
            "id": q.id,
            "question": q.question,
            "status": q.status,
            "created_at": q.created_at.isoformat(),
            "answered_at": q.answered_at.isoformat() if q.answered_at else None,
            "answer_guidance_id": q.answer_guidance_id,
        }
        for q in a.db.scalars(
            select(HumanQuestion)
            .where(HumanQuestion.room_id == room.id, HumanQuestion.workspace_id == a.workspace.id)
            .order_by(HumanQuestion.created_at.desc(), HumanQuestion.id.desc())
        )
    ]


def answer_questions(a, room, guidance):
    for question in a.db.scalars(
        select(HumanQuestion).where(
            HumanQuestion.room_id == room.id,
            HumanQuestion.workspace_id == a.workspace.id,
            HumanQuestion.status == "pending",
        )
    ):
        question.status, question.answered_at, question.answer_guidance_id = "answered", now(), guidance.id
