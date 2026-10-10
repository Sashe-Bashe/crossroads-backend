from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from .db import Base, now, uid


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=uid)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    active = Column(Boolean, default=True, nullable=False)


class Workspace(Base):
    __tablename__ = "workspaces"
    id = Column(String, primary_key=True, default=uid)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    __table_args__ = (CheckConstraint("role IN ('buyer','seller')"),)


class Membership(Base):
    __tablename__ = "memberships"
    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    workspace_id = Column(String, ForeignKey("workspaces.id"), primary_key=True)
    admin = Column(Boolean, default=False, nullable=False)
    approver = Column(Boolean, default=False, nullable=False)


class Credential(Base):
    __tablename__ = "credentials"
    id = Column(String, primary_key=True, default=uid)
    token_hash = Column(String, unique=True, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    scopes = Column(JSON, default=list, nullable=False)
    csrf_hash = Column(String)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)


class Product(Base):
    __tablename__ = "products"
    id = Column(String, primary_key=True, default=uid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    sku = Column(String, nullable=False)
    data = Column(JSON, nullable=False)
    stock = Column(Integer, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    published = Column(Boolean, default=False, nullable=False)
    cover_id = Column(String)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("workspace_id", "sku"), CheckConstraint("stock >= 0"))


class ProductRevision(Base):
    __tablename__ = "product_revisions"
    id = Column(String, primary_key=True, default=uid)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    version = Column(Integer, nullable=False)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("product_id", "version"),)


class Policy(Base):
    __tablename__ = "policies"
    id = Column(String, primary_key=True, default=uid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    target = Column(String, default="workspace", nullable=False)
    version = Column(Integer, nullable=False)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("workspace_id", "target", "version"),)


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    id = Column(String, primary_key=True, default=uid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    data = Column(JSON, nullable=False)
    state = Column(String, default="draft", nullable=False)
    version = Column(Integer, default=1, nullable=False)
    matches = Column(JSON, default=list, nullable=False)
    selected_offer_id = Column(String)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Room(Base):
    __tablename__ = "negotiations"
    id = Column(String, primary_key=True, default=uid)
    request_id = Column(String, ForeignKey("purchase_requests.id"), nullable=False, index=True)
    seller_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    state = Column(String, default="negotiating", nullable=False)
    version = Column(Integer, default=0, nullable=False)
    run = Column(Integer, default=1, nullable=False)
    next_role = Column(String, default="seller", nullable=False)
    current_offer_id = Column(String)
    claim_credential_id = Column(String, ForeignKey("credentials.id"))
    claim_token_hash = Column(String)
    claim_until = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("request_id", "product_id"),)


class Offer(Base):
    __tablename__ = "offers"
    id = Column(String, primary_key=True, default=uid)
    room_id = Column(String, ForeignKey("negotiations.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    sender = Column(String, nullable=False)
    terms = Column(JSON, nullable=False)
    product_snapshot = Column(JSON, nullable=False)
    policy_versions = Column(JSON, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("room_id", "version"),)


class Action(Base):
    __tablename__ = "actions"
    id = Column(String, primary_key=True, default=uid)
    room_id = Column(String, ForeignKey("negotiations.id"), nullable=False, index=True)
    credential_id = Column(String, ForeignKey("credentials.id"), nullable=False)
    key = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)
    type = Column(String, nullable=False)
    run = Column(Integer, default=1, nullable=False)
    data = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("room_id", "credential_id", "key"),)


class Approval(Base):
    __tablename__ = "approvals"
    id = Column(String, primary_key=True, default=uid)
    offer_id = Column(String, ForeignKey("offers.id"), nullable=False)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("offer_id", "workspace_id"),)


class OfferInvalidation(Base):
    __tablename__ = "offer_invalidations"
    offer_id = Column(String, ForeignKey("offers.id"), primary_key=True)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True, default=uid)
    request_id = Column(String, ForeignKey("purchase_requests.id"), unique=True, nullable=False)
    room_id = Column(String, ForeignKey("negotiations.id"), unique=True, nullable=False)
    offer_id = Column(String, ForeignKey("offers.id"), unique=True, nullable=False)
    buyer_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    seller_id = Column(String, ForeignKey("workspaces.id"), nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Allocation(Base):
    __tablename__ = "allocations"
    id = Column(String, primary_key=True, default=uid)
    order_id = Column(String, ForeignKey("orders.id"), unique=True, nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("quantity > 0"),)


class Image(Base):
    __tablename__ = "images"
    id = Column(String, primary_key=True, default=uid)
    product_id = Column(String, ForeignKey("products.id"), nullable=False, index=True)
    status = Column(String, default="pending", nullable=False)
    source_key = Column(String, nullable=False)
    object_key = Column(String)
    thumbnail_key = Column(String)
    mime = Column(String)
    width = Column(Integer)
    height = Column(Integer)
    checksum = Column(String)
    position = Column(Integer, nullable=False)
    caption = Column(String, default="", nullable=False)
    error = Column(String)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String, primary_key=True, default=uid)
    image_id = Column(String, ForeignKey("images.id"), unique=True, nullable=False)
    state = Column(String, default="pending", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    available_at = Column(DateTime(timezone=True), default=now, nullable=False)
    lease_until = Column(DateTime(timezone=True))
    lease_token = Column(String)


class Event(Base):
    __tablename__ = "events"
    id = Column(String, primary_key=True, default=uid)
    workspace_id = Column(String, ForeignKey("workspaces.id"), nullable=False, index=True)
    actor_id = Column(String, nullable=False)
    type = Column(String, nullable=False)
    resource_id = Column(String, nullable=False)
    data = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False, index=True)


class Idempotency(Base):
    __tablename__ = "idempotency"
    credential_id = Column(String, ForeignKey("credentials.id"), primary_key=True)
    key = Column(String, primary_key=True)
    operation = Column(String, nullable=False)
    request_hash = Column(String, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now, nullable=False)


class RateWindow(Base):
    __tablename__ = "rate_windows"
    key = Column(String, primary_key=True)
    count = Column(Integer, default=0, nullable=False)
    reset_at = Column(DateTime(timezone=True), nullable=False)


Index("ix_negotiations_participant_state", Room.seller_id, Room.state)
