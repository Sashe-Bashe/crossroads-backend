"""Typed public API DTOs and the exhaustive resource-envelope route contract.

Service functions deliberately return unwrapped domain values. The API router
wraps them exactly once, before FastAPI validates and serializes the response.
This keeps internal endpoint composition and stored idempotency results stable.
"""

from datetime import date, datetime
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, Generic, Literal, TypeVar

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, create_model

from .schemas import BuyerPolicy, ProductInput, SellerPolicy

Role = Literal["buyer", "seller"]
State = Literal[
    "draft",
    "no_match",
    "negotiating",
    "needs_revision",
    "awaiting_approval",
    "needs_human_review",
    "declined",
    "expired",
    "cancelled",
    "ordered",
]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(DTO):
    field: str
    message: str


class Error(DTO):
    code: str = Field(description="Stable machine-readable error identifier.")
    message: str = Field(description="Safe explanation; sensitive inputs are never echoed.")
    details: list[ErrorDetail] | dict[str, Any] | None


class ErrorResponse(DTO):
    error: Error
    status_code: int = Field(ge=400, le=599, description="Always equals the HTTP response status.")


class Health(DTO):
    status: Literal["ok"]
    api_version: str
    currency: Literal["EUR"]


class Token(DTO):
    token: str = Field(description="Opaque bearer secret. Store privately; shown only at issuance.")
    credential_id: str
    expires_at: datetime
    kind: Literal["cli"]


class SessionResult(DTO):
    ok: bool


class Workspace(DTO):
    id: str
    name: str
    role: Role


class User(DTO):
    id: str
    name: str
    email: str


class CurrentCredential(DTO):
    id: str
    kind: Literal["cli", "browser", "agent"]
    scopes: list[str]


class Me(DTO):
    user: User
    workspace: Workspace
    credential: CurrentCredential
    can_approve: bool
    demo: bool


class Credential(DTO):
    id: str
    name: str
    scopes: list[str]
    revoked: bool
    expires_at: datetime


class IssuedCredential(DTO):
    id: str
    name: str
    token: str
    workspace_id: str
    role: Role
    scopes: list[str]
    expires_at: datetime


class RevokedCredential(DTO):
    id: str
    revoked: Literal[True]


class CategoryAttributes(DTO):
    material: list[str]
    stackable: Literal["boolean"]
    color: Literal["string"]


class Category(DTO):
    id: Literal["chairs"]
    name: str
    attributes: CategoryAttributes


class ImageAsset(DTO):
    id: str
    product_id: str
    status: Literal["pending", "ready", "failed", "deleted"]
    mime: str | None
    width: int | None
    height: int | None
    sha256: str | None
    position: int
    caption: str
    error: str | None
    content_path: str
    thumbnail_path: str


class DeletedImage(DTO):
    id: str
    status: Literal["deleted"]


class Product(ProductInput):
    id: str
    workspace_id: str
    supplier: str
    version: int
    cover_id: str | None
    images: list[ImageAsset]


class ProductRevision(DTO):
    version: int
    product: Product
    at: datetime


class Rules(DTO):
    role: Role
    target: str
    version: int
    rules: SellerPolicy | BuyerPolicy
    effective: SellerPolicy | BuyerPolicy
    versions: dict[str, int]


class RuleRevision(DTO):
    version: int
    rules: SellerPolicy | BuyerPolicy
    at: datetime


class Match(DTO):
    product_id: str
    supplier: str
    eligible: bool
    reasons: list[str]


class PurchaseRequest(DTO):
    """Buyer-private fields are omitted entirely from seller-visible requests."""

    id: str
    workspace_id: str
    state: State
    version: int
    created_at: datetime
    title: str
    category: Literal["chairs"]
    quantity: int
    material: Literal["any", "polypropylene", "wood", "fabric"]
    stackable: bool
    destination: str
    latest_delivery: date
    description: str | None = None
    max_total: int | None = None
    max_deposit_percent: int | None = None
    preference: Literal["lowest_total", "lower_deposit", "earliest_delivery"] | None = None
    matches: list[Match] | None = None


class CommercialTerms(DTO):
    unit_price: int = Field(description="Integer EUR cents per unit.")
    quantity: int
    delivery_date: date
    deposit_percent: int
    currency: Literal["EUR"]
    delivery_charge: int = Field(description="Integer EUR cents, charged once.")
    total: int = Field(description="unit_price × quantity + delivery_charge, in EUR cents.")
    tax_treatment: str


class Approval(DTO):
    workspace_id: str
    user_id: str
    at: datetime


class RequirementEvidence(DTO):
    source: Literal["offer", "product", "request"]
    path: str
    value: str | int | bool


class Requirement(DTO):
    id: str
    label: str
    kind: Literal["hard", "preference"]
    field: Literal[
        "quantity",
        "material",
        "stackable",
        "destination",
        "delivery_date",
        "total",
        "deposit_percent",
        "unit_price",
        "unsupported",
    ]
    operator: Literal["eq", "lte", "gte"]
    value: str | int | bool | None
    shareable: bool
    resolution: Literal["open", "waived"]
    resolution_note: str | None = None
    status: Literal["met", "unmet", "unverified", "waived"]
    evidence: list[RequirementEvidence]
    blocks_agreement: bool


class BusinessBrief(DTO):
    version: int
    goals: str
    shareable_needs: str
    requirements: list[Requirement]


class SharedBrief(DTO):
    role: Role
    version: int
    shareable_needs: str
    requirements: list[Requirement]


class HumanQuestion(DTO):
    id: str
    question: str
    status: Literal["pending", "answered"]
    created_at: datetime
    answered_at: datetime | None
    answer_guidance_id: str | None


class Offer(DTO):
    id: str
    version: int
    sender: Role
    terms: CommercialTerms
    product: Product
    expires_at: datetime
    created_at: datetime
    approvals: list[Approval]
    private_brief_review: BusinessBrief | None = None
    shared_brief_review: list[SharedBrief] = Field(default_factory=list)


class ActionData(DTO):
    offer_id: str | None = None
    offer_version: int | None = None
    topic: Literal["specifications", "delivery", "images", "quantity"] | None = None
    message: str | None = None
    role: Role | None = None
    run: int | None = None
    purpose: Literal["question", "answer", "tradeoff", "explanation"] | None = None
    reply_to_action_id: str | None = None
    image_ids: list[str] = Field(default_factory=list)


class RoomActivity(DTO):
    id: str
    role: Role
    type: str
    data: ActionData
    at: datetime
    run: int


class HumanGuidance(DTO):
    id: str
    role: Role
    instruction: str
    status: Literal["pending", "applied", "superseded"]
    run: int
    created_at: datetime
    applied_at: datetime | None


class Room(DTO):
    id: str
    request_id: str
    seller_id: str
    supplier: str
    product_id: str
    state: State
    version: int
    run: int
    next_role: Role
    reason: str | None
    expires_at: datetime
    agent_status: Literal["claimed", "waiting_for_agent"]
    request: PurchaseRequest
    product: Product
    offers: list[Offer]
    current_offer: Offer | None
    valid: bool
    invalid_reasons: list[str]
    activity: list[RoomActivity]
    human_guidance: list[HumanGuidance] = Field(
        description="Private guidance history belonging only to the selected workspace, newest first. Never share with the counterparty."
    )
    can_request_guidance: bool
    private_brief: BusinessBrief
    shared_briefs: list[SharedBrief]
    human_questions: list[HumanQuestion]
    can_edit_brief: bool
    requirement_blockers: list[str]
    approval_url: str


class RequestDetail(DTO):
    request: PurchaseRequest
    negotiations: list[Room]


class Comparison(DTO):
    request_id: str
    preference: Literal["lowest_total", "lower_deposit", "earliest_delivery"]
    best_fit_room_id: str | None
    lowest_price_room_id: str | None
    negotiations: list[Room]


class BuyerContextRules(BuyerPolicy):
    preference: Literal["lowest_total", "lower_deposit", "earliest_delivery"]


class NegotiationLimits(DTO):
    counteroffers_per_side: int
    clarification_turns: int
    conversation_turns: int
    conversation_turns_remaining: int
    offer_actions_remaining: int


class NegotiationContext(Room):
    role: Role
    private_rules: SellerPolicy | BuyerContextRules
    policy_versions: dict[str, int]
    allowed_actions: list[str]
    limits: NegotiationLimits
    instructions: str


class Claim(DTO):
    claim_token: str
    room_id: str
    expected_version: int
    expires_at: datetime


class ActionResult(ActionData):
    room_id: str
    state: State
    version: int
    approval_url: str | None


class Order(DTO):
    id: str
    request_id: str
    room_id: str
    buyer_id: str
    seller_id: str
    created_at: datetime
    offer: Offer
    buyer: str
    seller: str
    status: Literal["confirmed_demo"]
    private_brief_review: BusinessBrief | None = None
    shared_brief_review: list[SharedBrief] = Field(default_factory=list)


class ApprovalResult(DTO):
    state: Literal["awaiting_approval", "ordered", "declined"]
    order: Order | None


class AuditEvent(DTO):
    id: str
    actor_id: str
    type: str
    resource_id: str
    data: dict[str, Any] = Field(description="Event-type-specific public audit metadata.")
    at: datetime


class Instructions(DTO):
    api_version: str
    role: Role
    workspace_id: str
    scopes: list[str]
    role_permissions: dict[Role, list[str]]
    currency: Literal["EUR"]
    money_unit: Literal["integer cents"]
    workflow: list[str]
    rules: list[str]
    action_schema: dict[str, Any] = Field(description="JSON Schema for ActionInput.")
    request_schema: dict[str, Any] = Field(description="JSON Schema for RequestInput.")
    product_schema: dict[str, Any] = Field(description="JSON Schema for ProductInput.")


class InboxItem(DTO):
    room_id: str
    state: State
    next_role: Role
    version: int
    can_act: bool
    context_path: str
    pending_guidance_id: str | None


T = TypeVar("T")


class Items(DTO, Generic[T]):
    items: list[T]


class Page(Items[T], Generic[T]):
    total: int
    offset: int
    limit: int


class ActivityPage(Items[AuditEvent]):
    offset: int
    limit: int


# endpoint: (resource label, payload model, convert raw list to {items: [...]})
# This table is exhaustive: adding an API route without a contract fails at startup.
CONTRACTS = {
    "health": ("health", Health, False),
    "cli_login": ("token", Token, False),
    "browser_login": ("session", SessionResult, False),
    "logout": ("session", SessionResult, False),
    "me": ("identity", Me, False),
    "workspaces": ("workspaces", Items[Workspace], True),
    "credentials": ("credentials", Items[Credential], True),
    "credentials_create": ("credential", IssuedCredential, False),
    "credentials_revoke": ("credential", RevokedCredential, False),
    "categories": ("categories", Items[Category], True),
    "products": ("products", Page[Product], False),
    "products_create": ("product", Product, False),
    "products_show": ("product", Product, False),
    "products_update": ("product", Product, False),
    "products_stock": ("product", Product, False),
    "products_history": ("product_history", Items[ProductRevision], True),
    "images_list": ("images", Items[ImageAsset], True),
    "images_upload": ("image", ImageAsset, False),
    "images_cover": ("product", Product, False),
    "images_order": ("product", Product, False),
    "images_remove": ("image", DeletedImage, False),
    "rules": ("rules", Rules, False),
    "rules_history": ("rule_history", Items[RuleRevision], True),
    "rules_update": ("rules", Rules, False),
    "requests": ("requests", Page[PurchaseRequest], False),
    "requests_create": ("request", PurchaseRequest, False),
    "requests_show": ("request_detail", RequestDetail, False),
    "requests_update": ("request", PurchaseRequest, False),
    "requests_launch": ("request", PurchaseRequest, False),
    "requests_cancel": ("request", PurchaseRequest, False),
    "requests_matches": ("matches", Items[Match], True),
    "requests_compare": ("comparison", Comparison, False),
    "negotiations": ("negotiations", Page[Room], False),
    "negotiations_show": ("negotiation", Room, False),
    "negotiations_context": ("context", NegotiationContext, False),
    "negotiations_messages": ("messages", Page[RoomActivity], False),
    "negotiations_brief": ("brief", BusinessBrief, False),
    "negotiations_brief_update": ("brief", BusinessBrief, False),
    "negotiations_claim": ("claim", Claim, False),
    "negotiations_act": ("action", ActionResult, False),
    "negotiations_guidance": ("negotiation", Room, False),
    "negotiations_retry": ("negotiation", Room, False),
    "approval_status": ("negotiation", Room, False),
    "approval_accept": ("approval", ApprovalResult, False),
    "approval_reject": ("approval", ApprovalResult, False),
    "orders": ("orders", Page[Order], False),
    "orders_show": ("order", Order, False),
    "activity": ("activity", ActivityPage, False),
    "instructions": ("instructions", Instructions, False),
    "inbox": ("inbox", Items[InboxItem], False),
}

DESCRIPTIONS = {
    "health": "Check API/database readiness and the supported currency without authentication.",
    "cli_login": "Exchange a user's email and password for a 24-hour opaque CLI bearer token. Login attempts are rate limited.",
    "browser_login": "Create a 12-hour HttpOnly browser session and a readable CSRF cookie. Requires an allowed Origin; no bearer token is returned.",
    "logout": "Revoke the current credential and clear both browser cookies. Browser sessions require Origin and CSRF validation.",
    "me": "Inspect the authenticated user, selected workspace, effective credential scopes and browser approval capability.",
    "workspaces": "List memberships available to this user. A workspace-bound agent can only see its bound workspace.",
    "credentials": "List the selected workspace's agent credentials without exposing their secrets. Requires a user administrator.",
    "credentials_create": "Issue a workspace-bound agent token with a subset of the workspace's buyer or seller scopes. Only user administrators may issue tokens; the secret is returned once.",
    "credentials_revoke": "Revoke an agent credential owned by the selected workspace immediately. Requires a user administrator.",
    "categories": "Read supported catalogue categories and their structured attribute vocabulary.",
    "products": "Search and filter published catalogue products plus the selected seller's own drafts. Pagination describes the filtered result set.",
    "products_create": "Create a seller-owned product and its first immutable revision. Requires products:write, seller role and Idempotency-Key.",
    "products_show": "Read a published product or the selected seller's own draft. Only the owner sees pending or failed images.",
    "products_update": "Replace a seller-owned product using its current expected_version. Creates a revision and invalidates affected offers and approvals.",
    "products_stock": "Set seller-owned inventory using expected_version. Inventory changes invalidate affected offers and approvals.",
    "products_history": "Read immutable catalogue revisions belonging to the product's seller. Other workspaces cannot inspect revision history.",
    "images_list": "List visible image metadata in gallery order. Owners see processing states; other catalogue viewers see ready images only.",
    "images_upload": "Upload a JPEG, PNG or WebP to a seller-owned product. Requires multipart file, images:write and Idempotency-Key. A 202 pending result means processing was queued; poll the gallery for ready or failed.",
    "images_cover": "Choose a ready image as the seller-owned product's cover, using the current product expected_version.",
    "images_order": "Set the complete ordering of ready gallery image IDs, using the current product expected_version.",
    "images_remove": "Remove an image from the seller-owned catalogue using expected_version. Retained order evidence remains accessible to its participants.",
    "rules": "Read only the selected workspace's private rules, their versions and effective intersection. Product rules are visible only to their owning seller.",
    "rules_history": "Read versioned private workspace or product negotiation rules. The same ownership checks as the current-rule endpoint apply.",
    "rules_update": "Replace versioned negotiation rules as a user administrator. Product overrides cannot weaken workspace limits; changes invalidate affected offers and approvals.",
    "requests": "List the selected buyer workspace's private purchase requests. Seller credentials cannot use this endpoint.",
    "requests_create": "Create a buyer-owned private request within workspace budget and deposit limits. Requires requests:write and Idempotency-Key.",
    "requests_show": "Read a buyer-owned private request together with all its supplier negotiation rooms.",
    "requests_update": "Replace a draft or unmatched buyer request using expected_version. Active negotiations require the documented retry workflow.",
    "requests_launch": "Match a draft request against published products and open eligible supplier rooms. Requires a stable Idempotency-Key and permits at most 50 eligible products.",
    "requests_cancel": "Cancel a buyer request and its negotiations and remove pending approvals. Confirmed orders cannot be cancelled here.",
    "requests_matches": "Read the buyer's last matching results, including eligible suppliers and concrete exclusion reasons.",
    "requests_compare": "Compare the buyer's current supplier offers and identify the lowest price and best fit for the private preference.",
    "negotiations": "List only rooms in which the selected workspace is a buyer or seller participant. Seller-visible requests omit private buyer fields.",
    "negotiations_show": "Read a participant-scoped room, immutable offers, approvals, product evidence and activity.",
    "negotiations_context": "Read participant dialogue, explicitly shared needs and only the selected workspace's private rules, brief, requirement blockers, guidance and owner questions. Includes allowed actions and remaining conversation/offer allowances. Offer review snapshots preserve the exact needs considered when that offer was created.",
    "negotiations_messages": "Read paginated public dialogue and offer explanations for a participant room, optionally filtered by run. Private owner questions, briefs and guidance are excluded.",
    "negotiations_brief": "Read only the selected workspace's private business goals and server-evaluated needs. Agents may read but cannot edit or waive needs.",
    "negotiations_brief_update": "An owner user (browser or CLI, never agent) replaces its versioned private brief and explicitly shareable needs. Requires expected_version, expected_brief_version and Idempotency-Key. Starts a new bounded attempt and invalidates prior claims, offers and approvals. Unsupported hard needs block agreement until an owner explicitly resolves them. This operation does not answer private questions or call an LLM.",
    "negotiations_claim": "Claim the current role's turn for 180 seconds by default. Returns a secret claim_token and expected_version required for the action.",
    "negotiations_act": "Submit dialogue, structured terms, own-offer revisions or private owner questions using an active claim, expected_version, pending guidance_id and stable Idempotency-Key. Dialogue is bounded and cannot create contractual terms. Hard unmet/unverified needs block acceptance; agents cannot waive them. Accepting the exact current counterparty offer moves to both human approvals and never creates an order directly.",
    "negotiations_guidance": "An authorized human browser approver privately directs their own external agent to negotiate again. Requires CSRF, expected_version and Idempotency-Key. Starts a fresh bounded attempt on that role's turn, revokes current claims and approvals, and permanently invalidates the previous offer. Text is visible only to the requesting workspace. Agents must acknowledge their latest pending guidance ID on their next successful action. The API does not start or call an LLM.",
    "negotiations_retry": "Start a new bounded attempt for an expired, declined or review-stage room. Requires a buyer user credential; agents cannot retry. Existing offer history is retained.",
    "approval_status": "Read the participant-scoped room and the exact current offer's human approval status.",
    "approval_accept": "Approve the exact current offer using offer_id and expected_version. Both workspace approvals are required; the second approval atomically allocates stock and creates one order.",
    "approval_reject": "Reject the exact current approval-stage offer and clear its pending approvals.",
    "orders": "List confirmed orders in which the selected workspace is buyer or seller, including retained evidence and both approvals.",
    "orders_show": "Read a confirmed order's immutable terms, product evidence and approvals. Only its buyer and seller may access it.",
    "activity": "Read workspace-visible audit events, optionally filtered by resource ID. This feed uses offset/limit pagination without a total count.",
    "instructions": "Read role-specific agent instructions, effective scopes and JSON Schemas for structured requests, products and actions.",
    "inbox": "Read up to 100 participant rooms with current turn availability and links to role-scoped context.",
}

ERROR_RESPONSES = {
    code: {"model": ErrorResponse, "description": description}
    for code, description in {
        400: "Malformed request or unsupported action.",
        401: "Missing, expired or revoked authentication.",
        403: "Role, scope, workspace, browser approval or CSRF check failed.",
        404: "Resource does not exist or is not visible to this workspace.",
        405: "HTTP method is not supported by this resource.",
        409: "Stale version, conflicting state, active claim or idempotency conflict.",
        413: "Request body or image exceeds the configured limit.",
        422: "Input validation, commercial policy or eligibility check failed.",
        429: "Rate limit exceeded. Retry after the current rate window.",
        500: "Unexpected server failure. Details are not disclosed.",
    }.items()
}

IDEMPOTENT_OPERATIONS = {
    "products_create",
    "requests_create",
    "requests_launch",
    "images_upload",
    "negotiations_act",
    "negotiations_guidance",
    "negotiations_brief_update",
}


class ResourceRouter(APIRouter):
    def add_api_route(self, path, endpoint, **kwargs):
        name = endpoint.__name__
        tag = path.strip("/").split("/")[0].title()
        kwargs["operation_id"] = kwargs.get("operation_id") or name
        kwargs["tags"] = [tag]
        kwargs["responses"] = {**ERROR_RESPONSES, **(kwargs.get("responses") or {})}
        public = name in ("health", "cli_login", "browser_login")
        extra = {**(kwargs.get("openapi_extra") or {})}
        extra["security"] = [] if public else [{"BearerAuth": []}, {"BrowserSession": []}]
        browser_only = name in ("approval_accept", "approval_reject", "negotiations_guidance")
        if browser_only:
            extra["security"] = [{"BrowserSession": []}]
        parameters = []
        if not public:
            parameters.append(
                {
                    "name": "X-Workspace-ID",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": "Select a user membership. A workspace-bound agent cannot select another workspace.",
                }
            )
        methods = kwargs.get("methods") or []
        if name == "browser_login" or (
            not public and any(method in {"POST", "PUT", "PATCH", "DELETE"} for method in methods)
        ):
            parameters.append(
                {
                    "name": "Origin",
                    "in": "header",
                    "required": name == "browser_login" or browser_only,
                    "schema": {"type": "string"},
                    "description": "Required for browser writes; must match a configured allowed origin.",
                }
            )
            if name != "browser_login":
                parameters.append(
                    {
                        "name": "X-CSRF-Token",
                        "in": "header",
                        "required": browser_only,
                        "schema": {"type": "string"},
                        "description": "Required for browser writes; copy the readable market_csrf cookie. Bearer writes do not use CSRF.",
                    }
                )
        if name == "image_content":
            parameters.append(
                {
                    "name": "workspace_id",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": "Optional workspace selection for browser image elements; membership is always rechecked.",
                }
            )
        extra["parameters"] = [*(extra.get("parameters") or []), *parameters]
        if name == "image_content":
            extra["x-response-format"] = "binary"
            kwargs["response_class"] = Response
            kwargs["responses"][200] = {
                "description": "Authorized normalized JPEG bytes, or its JPEG thumbnail.",
                "content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}},
            }
            kwargs["description"] = (
                "Read actual product image bytes. Successful image responses are binary; failures use the JSON error envelope. Authorization is rechecked for catalogue or retained order evidence."
            )
        else:
            label, model, raw_list = CONTRACTS[name]
            status = kwargs.get("status_code") or 200
            response_model = create_model(
                "".join(part.title() for part in name.split("_")) + "Response",
                __base__=DTO,
                **{
                    label: (model, ...),
                    "status_code": (Literal[status], Field(description="Equals the HTTP response status.")),
                },
            )
            kwargs["response_model"] = response_model
            kwargs["response_model_exclude_unset"] = True
            extra["x-object-label"] = label
            kwargs["description"] = (
                DESCRIPTIONS[name] + f"\n\nReturns `{label}` and `status_code` ({status}). "
                "All failures use `error` and the matching HTTP `status_code`. "
                + (
                    "Public endpoint. "
                    if public
                    else "Requires an authorized workspace membership and applicable role/scopes. "
                )
                + ("The payload contains an `items` array. " if raw_list else "")
                + (
                    "Only an approver's browser session with valid Origin and X-CSRF-Token may decide the exact current offer."
                    if name in ("approval_accept", "approval_reject")
                    else ""
                )
            )
            original = endpoint
            if iscoroutinefunction(original):

                @wraps(original)
                async def wrapped(*args, **kw):
                    value = await original(*args, **kw)
                    return {label: {"items": value} if raw_list else value, "status_code": status}
            else:

                @wraps(original)
                def wrapped(*args, **kw):
                    value = original(*args, **kw)
                    return {label: {"items": value} if raw_list else value, "status_code": status}

            endpoint = wrapped
        kwargs["openapi_extra"] = extra
        return super().add_api_route(path, endpoint, **kwargs)
