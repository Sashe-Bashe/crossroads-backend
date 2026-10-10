import hashlib
import warnings
from datetime import timedelta
from io import BytesIO

from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError
from sqlalchemy import func, or_, select

from . import storage
from .config import settings
from .db import SessionLocal, now, uid
from .errors import require
from .models import Image, Job, Order, Product
from .service import (
    event,
    image_view,
    invalidate_product,
    mutation,
    product_for,
    product_view,
    snapshot_product,
)

PILImage.MAX_IMAGE_PIXELS = settings.max_image_pixels


def owned_media_product(a, product_id):
    a.need("images:write", "seller")
    p = product_for(a, product_id)
    require(
        p.workspace_id == a.workspace.id,
        "permission_denied",
        "Only the product's seller can edit its images",
        403,
    )
    return p


def upload(a, product_id, content, caption):
    p = owned_media_product(a, product_id)
    require(
        0 < len(content) <= settings.max_image_bytes,
        "image_size",
        "Image must be nonempty and within the configured size limit",
        413,
    )
    count = a.db.scalar(
        select(func.count())
        .select_from(Image)
        .where(Image.product_id == p.id, Image.status.in_(["pending", "ready"]))
    )
    require(count < settings.max_product_images, "image_limit", "Product image limit reached", 422)
    # Decode headers now; full verification, normalization and thumbnails run in the worker.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(BytesIO(content)) as img:
                require(img.format in ("JPEG", "PNG", "WEBP"), "image_format", "Use JPEG, PNG or WebP", 422)
                require(
                    img.width * img.height <= settings.max_image_pixels,
                    "image_dimensions",
                    "Image pixel limit exceeded",
                    422,
                )
                require(
                    not getattr(img, "is_animated", False),
                    "image_format",
                    "Animated images are not supported",
                    422,
                )
                img.verify()
    except (
        UnidentifiedImageError,
        OSError,
        PILImage.DecompressionBombError,
        PILImage.DecompressionBombWarning,
    ):
        from .errors import Problem

        raise Problem("invalid_image", "The file is not a valid supported image", 422)
    image_id = uid()
    key = f"staging/{a.workspace.id}/{image_id}"
    storage.put(key, content)
    image = Image(id=image_id, product_id=p.id, source_key=key, position=count, caption=caption)
    a.db.add(image)
    a.db.flush()
    a.db.add(Job(image_id=image.id))
    event(a.db, a.workspace.id, a.credential.id, "image_uploaded", p.id, {"image_id": image.id})
    return image_view(image)


def authorize_image(a, image_id):
    a.need("images:read")
    image = a.db.get(Image, image_id)
    require(image is not None, "image_not_found", "Image not found", 404)
    p = a.db.get(Product, image.product_id)
    permitted = p.workspace_id == a.workspace.id or (p.published and image.status == "ready")
    if not permitted:
        orders = a.db.scalars(
            select(Order).where(or_(Order.buyer_id == a.workspace.id, Order.seller_id == a.workspace.id))
        )
        permitted = any(
            image_id in [i["id"] for i in o.snapshot["offer"]["product"]["images"]] for o in orders
        )
    require(
        permitted and image.object_key and image.status in ("ready", "deleted"),
        "image_not_found",
        "Image content is not available to this workspace",
        404,
    )
    return image


def update_gallery(a, product_id, data, cover=False):
    p = owned_media_product(a, product_id)
    require(p.version == data.expected_version, "stale_version", "Refresh the gallery before editing", 409)
    images = list(a.db.scalars(select(Image).where(Image.product_id == p.id, Image.status == "ready")))
    ids = {i.id for i in images}
    if cover:
        require(data.image_id in ids, "invalid_image", "Cover must be a ready image of this product", 422)
        p.cover_id = data.image_id
    else:
        require(
            len(data.image_ids) == len(ids) and set(data.image_ids) == ids,
            "invalid_image_order",
            "Include every ready product image exactly once",
            422,
        )
        for image in images:
            image.position = data.image_ids.index(image.id)
    p.version += 1
    invalidate_product(a.db, p.id, "Product gallery changed")
    snapshot_product(a.db, p)
    event(a.db, a.workspace.id, a.credential.id, "gallery_updated", p.id)
    return product_view(a.db, p, True)


def delete_image(a, product_id, image_id, expected_version):
    p = owned_media_product(a, product_id)
    require(p.version == expected_version, "stale_version", "Refresh product before removing an image", 409)
    image = a.db.get(Image, image_id)
    require(image is not None and image.product_id == p.id, "image_not_found", "Image not found", 404)
    image.status = "deleted"
    if p.cover_id == image.id:
        p.cover_id = a.db.scalar(
            select(Image.id)
            .where(Image.product_id == p.id, Image.status == "ready", Image.id != image.id)
            .order_by(Image.position)
        )
    p.version += 1
    invalidate_product(a.db, p.id, "Product image removed")
    snapshot_product(a.db, p)
    event(a.db, a.workspace.id, a.credential.id, "image_deleted", p.id, {"image_id": image.id})
    return {"id": image.id, "status": "deleted"}


def process_one():
    token = uid()
    with SessionLocal.begin() as db:
        job = db.scalar(
            select(Job)
            .where(
                or_(
                    (Job.state == "pending") & (Job.available_at <= now()),
                    (Job.state == "running") & (Job.lease_until < now()),
                )
            )
            .order_by(Job.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return False
        job.state, job.lease_token, job.lease_until = "running", token, now() + timedelta(minutes=3)
        job.attempts += 1
        image = db.get(Image, job.image_id)
        image_id, job_id, source_key = image.id, job.id, image.source_key
    try:
        content = storage.get(source_key)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PILImage.DecompressionBombWarning)
            with PILImage.open(BytesIO(content)) as original:
                img = ImageOps.exif_transpose(original).convert("RGB")
                require(
                    img.width * img.height <= settings.max_image_pixels,
                    "image_dimensions",
                    "Image pixel limit exceeded",
                    422,
                )
                img.thumbnail((2400, 2400))
                width, height = img.size
                display = BytesIO()
                img.save(display, format="JPEG", quality=90)
                display_bytes = display.getvalue()
                img.thumbnail((480, 480))
                thumb = BytesIO()
                img.save(thumb, format="JPEG", quality=85)
        key, thumbnail_key = f"images/{image_id}/display.jpg", f"images/{image_id}/thumbnail.jpg"
        storage.put(key, display_bytes, "image/jpeg")
        storage.put(thumbnail_key, thumb.getvalue(), "image/jpeg")
        with SessionLocal.begin() as db:
            mutation(db)
            job, image = db.get(Job, job_id), db.get(Image, image_id)
            if job.lease_token != token:
                return True
            if image.status != "deleted":
                image.object_key, image.thumbnail_key, image.mime = key, thumbnail_key, "image/jpeg"
                image.width, image.height, image.checksum = (
                    width,
                    height,
                    hashlib.sha256(display_bytes).hexdigest(),
                )
                image.status, image.error = "ready", None
                p = db.get(Product, image.product_id)
                if p.cover_id is None:
                    p.cover_id = image.id
                p.version += 1
                invalidate_product(db, p.id, "New product image available")
                snapshot_product(db, p)
                event(db, p.workspace_id, "worker", "image_ready", p.id, {"image_id": image.id})
            job.state, job.lease_token = "done", None
        storage.remove(source_key)
    except Exception:
        with SessionLocal.begin() as db:
            mutation(db)
            job, image = db.get(Job, job_id), db.get(Image, image_id)
            if job.lease_token == token:
                job.state = "failed" if job.attempts >= 3 else "pending"
                job.lease_token = None
                job.available_at = now() + timedelta(seconds=5 * job.attempts)
                if job.state == "failed" and image.status != "deleted":
                    image.status, image.error = (
                        "failed",
                        "Image processing failed after three attempts; re-upload the file",
                    )
    return True


def cleanup_staging():
    """Collect abandoned uploads/outputs; retain pending jobs and historical image evidence."""
    with SessionLocal() as db:
        images = list(db.scalars(select(Image)))
        active = {i.source_key for i in images if i.status == "pending"}
        retained = {key for i in images for key in (i.object_key, i.thumbnail_key) if key}
        pending_prefixes = {f"images/{i.id}/" for i in images if i.status == "pending"}
    for obj in storage.client().list_objects(settings.minio_bucket, prefix="staging/", recursive=True):
        if obj.object_name not in active and obj.last_modified < now() - timedelta(hours=24):
            storage.remove(obj.object_name)
    for obj in storage.client().list_objects(settings.minio_bucket, prefix="images/", recursive=True):
        prefix = obj.object_name.rsplit("/", 1)[0] + "/"
        if (
            obj.object_name not in retained
            and prefix not in pending_prefixes
            and obj.last_modified < now() - timedelta(hours=24)
        ):
            storage.remove(obj.object_name)
