import signal
import time

from sqlalchemy import delete, select

from .db import SessionLocal, now
from .media import cleanup_staging, process_one
from .models import Approval, Offer, PurchaseRequest, RateWindow, Room
from .service import clear_claim, mutation, refresh_request, room_event


def maintenance():
    with SessionLocal.begin() as db:
        mutation(db)
        for room in db.scalars(
            select(Room).where(
                Room.state.in_(["negotiating", "awaiting_approval", "needs_revision", "needs_human_review"])
            )
        ):
            offer = db.get(Offer, room.current_offer_id) if room.current_offer_id else None
            if room.expires_at <= now() or (
                room.state != "needs_revision" and offer and offer.expires_at <= now()
            ):
                room.state, room.reason = "expired", "Negotiation or current offer expired"
                room.version += 1
                clear_claim(room)
                if offer:
                    db.execute(delete(Approval).where(Approval.offer_id == offer.id))
                room_event(db, room, "worker", "negotiation_expired")
                refresh_request(db, db.get(PurchaseRequest, room.request_id))
        db.execute(delete(RateWindow).where(RateWindow.reset_at < now()))


def main():
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_maintenance, last_cleanup = 0, 0
    print("Crossroads media and expiry worker ready", flush=True)
    while not stopping:
        try:
            if time.monotonic() - last_maintenance > 10:
                maintenance()
                last_maintenance = time.monotonic()
            if time.monotonic() - last_cleanup > 300:
                cleanup_staging()
                last_cleanup = time.monotonic()
            if not process_one():
                time.sleep(0.5)
        except Exception:
            print("Worker operation failed; retrying without exposing request data", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
