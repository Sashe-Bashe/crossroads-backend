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
    unit_price: int = Field(gt=0, le=100_000_000)
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
    min_unit_price: int = Field(default=1, ge=1, le=100_000_000)
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
        "accept",
        "decline",
        "request_clarification",
        "answer_clarification",
        "request_human_review",
    ]
    expected_version: int = Field(ge=0)
    claim_token: str = Field(min_length=20, max_length=200)
    terms: Terms | None = None
    offer_id: str | None = Field(default=None, max_length=50)
    topic: Literal["specifications", "delivery", "images", "quantity"] | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.type in ("propose", "counter"):
            if self.terms is None or self.offer_id is not None or self.topic is not None:
                raise ValueError("Offer actions require only terms")
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
        return self


class ApprovalInput(Strict):
    offer_id: str = Field(min_length=1, max_length=50)
    expected_version: int = Field(ge=0)


class GalleryInput(Strict):
    image_ids: list[str] = Field(min_length=1, max_length=10)
    expected_version: int = Field(ge=1)


class CoverInput(Strict):
    image_id: str
    expected_version: int = Field(ge=1)
