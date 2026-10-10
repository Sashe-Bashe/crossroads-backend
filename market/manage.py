"""Explicit operator commands; never invoked automatically on web startup."""

from datetime import date, timedelta
from io import BytesIO

import typer
from PIL import Image as PILImage
from PIL import ImageDraw
from sqlalchemy import select

from . import storage
from .auth import passwords
from .config import settings
from .db import Base, SessionLocal
from .models import Image, Job, Membership, Policy, Product, User, Workspace
from .schemas import ProductInput
from .service import mutation, snapshot_product

app = typer.Typer(no_args_is_help=True)


@app.command("storage-init")
def storage_init():
    storage.initialize()
    print("Private product storage is ready")


def illustration(label, color, side=False):
    """Original, deterministic demo drawing, not a product photograph. CC0 fixture."""
    img = PILImage.new("RGB", (1000, 760), "#f1f0e9")
    draw = ImageDraw.Draw(img)
    draw.ellipse((210, 620, 810, 695), fill="#dadbd3")
    if side:
        draw.polygon([(430, 180), (520, 190), (560, 440), (480, 440)], fill=color)
        draw.polygon([(390, 440), (690, 440), (720, 480), (400, 480)], fill=color)
        draw.line([(450, 480), (420, 630)], fill="#3c4851", width=22)
        draw.line([(650, 480), (700, 630)], fill="#3c4851", width=22)
    else:
        draw.rounded_rectangle((340, 170, 680, 420), radius=65, fill=color)
        draw.polygon([(340, 410), (680, 410), (740, 490), (280, 490)], fill=color)
        for a, b in [
            ((330, 480), (300, 640)),
            ((690, 480), (720, 640)),
            ((380, 440), (390, 600)),
            ((640, 440), (630, 600)),
        ]:
            draw.line([a, b], fill="#3c4851", width=21)
    draw.text((55, 55), label + (" / SIDE" if side else " / FRONT"), fill="#283a38", font_size=30)
    draw.text((55, 710), "CROSSROADS / DEMO PRODUCT ILLUSTRATION", fill="#6a7771", font_size=19)
    output = BytesIO()
    img.save(output, "PNG")
    return output.getvalue()


@app.command()
def seed(images: bool = typer.Option(True, "--images/--no-images")):
    if not settings.demo_enabled:
        raise typer.BadParameter("Set DEMO_ENABLED=true explicitly to create fictional demo businesses")
    if images:
        storage.initialize()
    with SessionLocal.begin() as db:
        mutation(db)
        entries = [
            ("buyer", "Maya Chen", "Forma Studio", "buyer"),
            ("forma", "Elias Novak", "Forma Supply", "seller"),
            ("nord", "Sofia Berg", "Nord Office", "seller"),
            ("atelier", "Luca Rossi", "Atelier Works", "seller"),
        ]
        for key, name, company, role in entries:
            if db.get(Workspace, "ws-" + key):
                continue
            user = User(
                id=key,
                email=key + "@demo.local",
                name=name,
                password_hash=passwords.hash(settings.demo_password),
            )
            workspace = Workspace(id="ws-" + key, name=company, role=role)
            db.add_all([user, workspace])
            db.flush()
            db.add(Membership(user_id=user.id, workspace_id=workspace.id, admin=True, approver=True))
            rules = (
                {"max_total": 10000000, "max_deposit_percent": 100}
                if role == "buyer"
                else {
                    "min_unit_price": 1,
                    "max_discount_percent": 100,
                    "min_deposit_percent": 0,
                    "discount_min_quantity": 1,
                }
            )
            db.add(Policy(workspace_id=workspace.id, target="workspace", version=1, data=rules))
        for key, name, stock, price, delivery, lead, floor, deposit, threshold, discount, color in [
            ("forma", "Arc stack chair", 240, 9500, 24000, 12, 8300, 30, 50, 20, "#617f7d"),
            ("nord", "Loop meeting chair", 150, 11000, 16000, 8, 9900, 50, 60, 10, "#c4b599"),
            ("atelier", "Mono stack chair", 45, 8500, 20000, 21, 7900, 40, 100, 8, "#4d6753"),
        ]:
            pid = "prod-" + key
            if db.get(Product, pid):
                continue
            data = ProductInput(
                name=name,
                description="Fictional stocked chair for the local marketplace demonstration. Images are illustrations.",
                sku=key.upper() + "-01",
                attributes={
                    "material": "polypropylene",
                    "stackable": True,
                    "color": "Slate" if key == "forma" else "Sand" if key == "nord" else "Forest",
                },
                unit_price=price,
                delivery_charge=delivery,
                lead_days=lead,
                destinations=["Sofia", "Plovdiv"],
                stock=stock,
                published=True,
            ).model_dump(mode="json")
            p = Product(
                id=pid,
                workspace_id="ws-" + key,
                sku=data.pop("sku"),
                stock=data.pop("stock"),
                published=data.pop("published"),
                data=data,
            )
            db.add(p)
            db.flush()
            snapshot_product(db, p)
            db.add(
                Policy(
                    workspace_id=p.workspace_id,
                    target=p.id,
                    version=1,
                    data={
                        "min_unit_price": floor,
                        "max_discount_percent": discount,
                        "min_deposit_percent": deposit,
                        "discount_min_quantity": threshold,
                    },
                )
            )
            if images:
                for position in range(2):
                    image_id = "demo-" + key + "-" + str(position)
                    source = "staging/demo/" + image_id
                    storage.put(source, illustration(name, color, bool(position)))
                    image = Image(
                        id=image_id,
                        product_id=pid,
                        source_key=source,
                        position=position,
                        caption="Side illustration" if position else "Front illustration",
                    )
                    db.add(image)
                    db.flush()
                    db.add(Job(image_id=image.id))
    print("Demo businesses ready: buyer@demo.local, forma@demo.local, nord@demo.local, atelier@demo.local")
    print("Use DEMO_PASSWORD from your local configuration. Run the worker to process seeded images.")


@app.command("add-user")
def add_user(
    email: str = typer.Option(...),
    name: str = typer.Option(...),
    workspace: str = typer.Option(...),
    role: str = typer.Option(...),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
):
    if role not in ("buyer", "seller"):
        raise typer.BadParameter("Role must be buyer or seller")
    if len(password) < 12:
        raise typer.BadParameter("Use at least 12 characters")
    with SessionLocal.begin() as db:
        mutation(db)
        w = Workspace(name=workspace, role=role)
        u = User(email=email.lower(), name=name, password_hash=passwords.hash(password))
        db.add_all([w, u])
        db.flush()
        db.add(Membership(user_id=u.id, workspace_id=w.id, admin=True, approver=True))
    print("User and role-bound workspace created")


@app.command("reset-demo")
def reset_demo(
    confirm_demo_reset: bool = typer.Option(False, "--confirm-demo-reset"),
    images: bool = typer.Option(True, "--images/--no-images"),
):
    """Reset a demo-only database. Refuses mixed demo/non-demo workspaces or users."""
    if not settings.demo_enabled or not confirm_demo_reset:
        raise typer.BadParameter(
            "Requires DEMO_ENABLED=true and --confirm-demo-reset; all demo history will be removed"
        )
    demo_ids = {"buyer", "forma", "nord", "atelier"}
    with SessionLocal.begin() as db:
        mutation(db)
        users = set(db.scalars(select(User.id)))
        workspaces = set(db.scalars(select(Workspace.id)))
        if not users <= demo_ids or not workspaces <= {"ws-" + key for key in demo_ids}:
            raise typer.BadParameter("Refusing reset: this database contains non-demo users or workspaces")
        keys = {
            key
            for item in db.scalars(select(Image))
            for key in (item.source_key, item.object_key, item.thumbnail_key)
            if key
        }
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
    # Exact keys only. Never delete a bucket or unrelated storage prefixes.
    for key in keys:
        try:
            storage.remove(key)
        except Exception:
            print("An old demo object could not be removed; inspect storage before cleanup")
    seed(images=images)


@app.command("example-request")
def example_request():
    import json

    print(
        json.dumps(
            {
                "title": "80 stackable chairs",
                "description": "80 chairs for our Sofia office",
                "quantity": 80,
                "material": "polypropylene",
                "stackable": True,
                "destination": "Sofia",
                "latest_delivery": (date.today() + timedelta(days=14)).isoformat(),
                "max_total": 700000,
                "max_deposit_percent": 40,
                "preference": "lower_deposit",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
