from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Strict):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class CredentialInput(Strict):
    name: str = Field(min_length=2, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=15)
    expires_days: int = Field(default=30, ge=1, le=90)


class Attributes(Strict):
    material: Literal["polypropylene", "wood", "fabric"]
    stackable: bool
    color: str = Field(min_length=1, max_length=60)


class ProductInput(Strict):
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=2000)
    sku: str = Field(min_length=2, max_length=50)
    category: Literal["chairs"] = "chairs"
    attributes: Attributes
    unit_price: int = Field(
        gt=0,
        le=100_000_000,
        description="Public asking price in integer EUR cents. The seller's private minimum is configured separately in product negotiation rules.",
    )
    delivery_charge: int = Field(ge=0, le=100_000_000)
    lead_days: int = Field(ge=1, le=90)
    destinations: list[str] = Field(min_length=1, max_length=30)
    stock: int = Field(ge=0, le=1_000_000)
    published: bool = False

    @model_validator(mode="after")
    def cities(self):
        if any(not city.strip() or len(city) > 100 for city in self.destinations):
            raise ValueError("Destinations must be nonempty city names, at most 100 characters")
        self.destinations = [city.strip() for city in self.destinations]
        return self


class ProductUpdate(ProductInput):
    expected_version: int = Field(ge=1)


class StockInput(Strict):
    stock: int = Field(ge=0, le=1_000_000)
    expected_version: int = Field(ge=1)


class SellerPolicy(Strict):
    min_unit_price: int = Field(
        default=1,
        ge=1,
        le=100_000_000,
        description="Private minimum acceptable price per unit in EUR cents. The seller agent may negotiate higher prices; counterparties cannot read this rule.",
    )
    max_discount_percent: int = Field(default=100, ge=0, le=100)
    min_deposit_percent: int = Field(default=0, ge=0, le=100)
    discount_min_quantity: int = Field(default=1, ge=1, le=1_000_000)


class BuyerPolicy(Strict):
    max_total: int = Field(default=100_000_000, ge=1, le=100_000_000)
    max_deposit_percent: int = Field(default=100, ge=0, le=100)


class PolicyInput(Strict):
    expected_version: int = Field(ge=0)
    rules: dict


class RequestInput(Strict):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=3000)
    category: Literal["chairs"] = "chairs"
    quantity: int = Field(ge=1, le=10000)
    material: Literal["any", "polypropylene", "wood", "fabric"]
    stackable: bool
    destination: str = Field(min_length=2, max_length=100)
    latest_delivery: date
    max_total: int = Field(gt=0, le=100_000_000)
    max_deposit_percent: int = Field(ge=0, le=100)
    preference: Literal["lowest_total", "lower_deposit", "earliest_delivery"] = "lowest_total"


class RequestUpdate(RequestInput):
    expected_version: int = Field(ge=1)


class Terms(Strict):
    unit_price: int = Field(gt=0, le=100_000_000)
    quantity: int = Field(ge=1, le=10000)
    delivery_date: date
    deposit_percent: int = Field(ge=0, le=100)


class ActionInput(Strict):
    type: Literal[
        "propose",
        "counter",
        "revise",
        "message",
        "accept",
        "decline",
        "request_clarification",
        "answer_clarification",
        "request_human_review",
    ]
    expected_version: int = Field(ge=0)
    claim_token: str = Field(min_length=20, max_length=200)
    guidance_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="The ID of your workspace's pending human guidance, required on the next successful action after reading it. Never echo the instruction text publicly.",
    )
    terms: Terms | None = None
    offer_id: str | None = Field(default=None, max_length=50)
    topic: Literal["specifications", "delivery", "images", "quantity"] | None = None
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    purpose: Literal["question", "answer", "tradeoff", "explanation"] | None = None
    reply_to_action_id: str | None = Field(default=None, min_length=1, max_length=50)
    image_ids: list[str] = Field(default_factory=list, max_length=5)
    human_question: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def shape(self):
        if self.type in ("propose", "counter", "revise"):
            if self.terms is None or self.offer_id is not None or self.topic is not None:
                raise ValueError("Offer actions require only terms")
            if self.message is not None and self.purpose != "explanation":
                raise ValueError("An offer message requires purpose=explanation")
        elif self.type == "message":
            if (
                self.message is None
                or self.purpose is None
                or any(value is not None for value in (self.terms, self.offer_id, self.topic))
            ):
                raise ValueError("A message requires text and purpose, without terms, offer_id or topic")
        elif self.type == "accept":
            if not self.offer_id or self.terms is not None or self.topic is not None:
                raise ValueError("Accept requires only offer_id")
        elif self.type in ("request_clarification", "answer_clarification"):
            if self.topic is None or self.terms is not None or self.offer_id is not None:
                raise ValueError(
                    "Clarification actions require only a topic; confirmed facts come from the catalogue"
                )
        elif self.terms is not None or self.offer_id is not None or self.topic is not None:
            raise ValueError("This action takes no terms, offer_id or topic")
        if self.type not in ("message", "propose", "counter", "revise") and any(
            value is not None for value in (self.message, self.purpose, self.reply_to_action_id)
        ):
            raise ValueError("Public conversation fields require a message or offer action")
        if (
            self.purpose is not None or self.reply_to_action_id is not None or self.image_ids
        ) and self.message is None:
            raise ValueError("Conversation metadata requires a public message")
        if self.human_question is not None and self.type != "request_human_review":
            raise ValueError("Private human questions require request_human_review")
        if len(set(self.image_ids)) != len(self.image_ids) or any(
            not x or len(x) > 50 for x in self.image_ids
        ):
            raise ValueError("Image references must be unique IDs of at most 50 characters")
        return self


class RequirementInput(Strict):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=300)
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
    operator: Literal["eq", "lte", "gte"] = "eq"
    value: str | int | bool | None = None
    shareable: bool = False
    resolution: Literal["open", "waived"] = "open"
    resolution_note: str | None = Field(default=None, min_length=3, max_length=1000)

    @model_validator(mode="after")
    def supported_value(self):
        if self.resolution == "waived" and self.resolution_note is None:
            raise ValueError("An owner waiver requires a resolution_note")
        if self.field == "unsupported":
            if self.value is not None or self.operator != "eq":
                raise ValueError("Unsupported requirements have no executable value")
        elif self.field in ("unit_price", "total", "quantity", "deposit_percent"):
            if type(self.value) is not int or self.value < 0 or self.value > 100_000_000:
                raise ValueError("This requirement needs a nonnegative integer value")
            if self.field == "deposit_percent" and self.value > 100:
                raise ValueError("Deposit percentage cannot exceed 100")
        elif self.field == "stackable":
            if type(self.value) is not bool or self.operator != "eq":
                raise ValueError("Stackability needs a boolean equality")
        elif self.field in ("material", "destination"):
            if (
                not isinstance(self.value, str)
                or not self.value.strip()
                or len(self.value) > 100
                or self.operator != "eq"
            ):
                raise ValueError("This requirement needs a nonempty text equality")
            if self.field == "material" and self.value not in ("polypropylene", "wood", "fabric"):
                raise ValueError("Use a supported product material")
        elif self.field == "delivery_date":
            if not isinstance(self.value, str):
                raise ValueError("Delivery requirements use YYYY-MM-DD")
            parsed = date.fromisoformat(self.value)
            if parsed.isoformat() != self.value:
                raise ValueError("Delivery requirements use YYYY-MM-DD")
        return self


class BriefInput(Strict):
    expected_version: int = Field(ge=0)
    expected_brief_version: int = Field(ge=0)
    goals: str = Field(default="", max_length=4000)
    shareable_needs: str = Field(default="", max_length=2000)
    requirements: list[RequirementInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_requirements(self):
        if len({r.id for r in self.requirements}) != len(self.requirements):
            raise ValueError("Requirement IDs must be unique within a brief")
        return self


class ApprovalInput(Strict):
    offer_id: str = Field(min_length=1, max_length=50)
    expected_version: int = Field(ge=0)


class GuidanceInput(Strict):
    instruction: str = Field(
        min_length=1,
        max_length=2000,
        description="Private direction to this workspace's external agent. Cannot override policies or authorize an order.",
    )
    expected_version: int = Field(ge=0)


class GalleryInput(Strict):
    image_ids: list[str] = Field(min_length=1, max_length=10)
    expected_version: int = Field(ge=1)


class CoverInput(Strict):
    image_id: str
    expected_version: int = Field(ge=1)
