from datetime import date, timedelta

from sqlalchemy import select

from .errors import require
from .models import Policy
from .schemas import BuyerPolicy, SellerPolicy


def policy_rows(db, workspace_id, target="workspace"):
    return list(
        db.scalars(
            select(Policy)
            .where(Policy.workspace_id == workspace_id, Policy.target == target)
            .order_by(Policy.version)
        )
    )


def effective(db, workspace_id, role, product_id=None):
    rules = (BuyerPolicy() if role == "buyer" else SellerPolicy()).model_dump()
    versions = {}
    for target in ["workspace"] + ([product_id] if product_id else []):
        rows = policy_rows(db, workspace_id, target)
        if not rows:
            continue
        p = rows[-1]
        versions[target] = p.version
        for key, value in p.data.items():
            # The intersection of policies cannot relax workspace constraints.
            rules[key] = min(rules[key], value) if key.startswith("max_") else max(rules[key], value)
    return rules, versions


def match_reasons(product, request):
    p, r = product.data, request.data
    issues = []
    if not product.published:
        issues.append("Product is not published")
    if p["category"] != r["category"]:
        issues.append("Category does not match")
    if product.stock < r["quantity"]:
        issues.append(f"Only {product.stock} units are available; {r['quantity']} required")
    if r["material"] != "any" and p["attributes"]["material"] != r["material"]:
        issues.append("Required material is unavailable")
    if r["stackable"] and not p["attributes"]["stackable"]:
        issues.append("Stackable product required")
    if r["destination"].casefold() not in [x.casefold() for x in p["destinations"]]:
        issues.append("Destination is outside supplier coverage")
    if (date.today() + timedelta(days=p["lead_days"])).isoformat() > r["latest_delivery"]:
        issues.append("Supplier lead time misses the deadline")
    return issues


def check_terms(db, product, request, terms, side):
    issues = match_reasons(product, request)
    r, p = request.data, product.data
    if terms["quantity"] != r["quantity"]:
        issues.append("Quantity must match the request")
    earliest = date.today() + timedelta(days=p["lead_days"])
    if not earliest.isoformat() <= terms["delivery_date"] <= r["latest_delivery"]:
        issues.append("Delivery date is outside the permitted range")
    if side in ("seller", "both"):
        rules, _ = effective(db, product.workspace_id, "seller", product.id)
        floor = max(
            rules["min_unit_price"],
            (p["unit_price"] * (100 - rules["max_discount_percent"]) + 99) // 100,
            p["unit_price"] if terms["quantity"] < rules["discount_min_quantity"] else 0,
        )
        if terms["unit_price"] < floor or terms["deposit_percent"] < rules["min_deposit_percent"]:
            issues.append("Supplier commercial requirements are not satisfied")
    if side in ("buyer", "both"):
        rules, _ = effective(db, request.workspace_id, "buyer")
        if terms["unit_price"] * terms["quantity"] + p["delivery_charge"] > min(
            r["max_total"], rules["max_total"]
        ):
            issues.append("Buyer budget requirements are not satisfied")
        if terms["deposit_percent"] > min(r["max_deposit_percent"], rules["max_deposit_percent"]):
            issues.append("Buyer deposit requirements are not satisfied")
    return issues


def validate_request_policy(db, workspace_id, data):
    rules, _ = effective(db, workspace_id, "buyer")
    require(
        data["max_total"] <= rules["max_total"]
        and data["max_deposit_percent"] <= rules["max_deposit_percent"],
        "policy_violation",
        "Request limits cannot exceed workspace buyer limits",
        422,
    )
