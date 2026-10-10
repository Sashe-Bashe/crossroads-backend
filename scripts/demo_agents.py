"""Exercise the real CLI as two separately credentialed, scripted external agents.

Creates requests and offers, never approves an order. No LLM calls. Does not reset data.
"""

import json
import os
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("MARKET_API_URL", "http://127.0.0.1:8100").rstrip("/")
PASSWORD = os.environ.get("DEMO_PASSWORD", "local-market-demo")


def get_token(name, scopes):
    with httpx.Client(base_url=API + "/api/v1", trust_env=False) as c:
        res = c.post("/auth/token", json={"email": name + "@demo.local", "password": PASSWORD})
        res.raise_for_status()
        owner = res.json()["token"]
        res = c.post(
            "/credentials",
            headers={"Authorization": "Bearer " + owner},
            json={"name": "Scripted CLI demonstration", "scopes": scopes},
        )
        res.raise_for_status()
        return res.json()["token"]


def main():
    base = ["catalogue:read", "images:read", "negotiations:read", "negotiations:act", "orders:read"]
    buyer = get_token("buyer", base + ["requests:write"])
    sellers = {name: get_token(name, base) for name in ("forma", "nord")}
    with tempfile.TemporaryDirectory(prefix="market-cli-demo-") as scratch:

        def cli(token, *args):
            env = {
                **os.environ,
                "MARKET_TOKEN": token,
                "MARKET_API_URL": API,
                "MARKET_CONFIG": scratch + "/config.json",
            }
            result = subprocess.run(
                ["uv", "run", "--project", str(ROOT / "cli"), "market", *args, "--json"],
                env=env,
                text=True,
                capture_output=True,
            )
            if result.returncode:
                raise RuntimeError(result.stderr)
            return json.loads(result.stdout)

        def file(name, value):
            path = Path(scratch) / name
            path.write_text(json.dumps(value))
            return str(path)

        instructions = cli(buyer, "agent", "instructions")
        assert instructions["role"] == "buyer"
        products = cli(buyer, "products", "list")
        assert products["total"] >= 3
        photos = cli(
            buyer, "products", "images", "read", "prod-forma", "--all", "--output-dir", scratch + "/images"
        )
        assert len(photos["images"]) >= 2
        request = {
            "title": "80 chairs / CLI demonstration " + uuid4().hex[:6],
            "description": "80 stackable chairs for Sofia",
            "quantity": 80,
            "material": "polypropylene",
            "stackable": True,
            "destination": "Sofia",
            "latest_delivery": (date.today() + timedelta(days=14)).isoformat(),
            "max_total": 700000,
            "max_deposit_percent": 40,
            "preference": "lower_deposit",
        }
        r = cli(buyer, "requests", "create", "--file", file("request.json", request))
        cli(buyer, "requests", "launch", r["id"])
        rooms = cli(buyer, "requests", "show", r["id"])["negotiations"]

        def act(token, room, kind, **data):
            claimed = cli(token, "agent", "claim", room["id"])
            payload = {
                "type": kind,
                "expected_version": claimed["expected_version"],
                "claim_token": claimed["claim_token"],
                **data,
            }
            return cli(
                token,
                "negotiations",
                "act",
                room["id"],
                "--file",
                file("action.json", payload),
                "--idempotency-key",
                str(uuid4()),
            )

        for room in rooms:
            name = "forma" if room["product_id"] == "prod-forma" else "nord"
            t = {
                "unit_price": 9500 if name == "forma" else 11000,
                "quantity": 80,
                "delivery_date": (date.today() + timedelta(days=12 if name == "forma" else 8)).isoformat(),
                "deposit_percent": 50,
            }
            act(sellers[name], room, "propose", terms=t)
            counter = act(buyer, room, "counter", terms={**t, "unit_price": 8300, "deposit_percent": 30})
            if name == "forma":
                agreed = act(sellers[name], room, "accept", offer_id=counter["offer_id"])
                room_id = room["id"]
            else:
                act(sellers[name], room, "decline")
        comparison = cli(buyer, "requests", "compare", r["id"])
        assert comparison["best_fit_room_id"] == room_id
        print(
            json.dumps(
                {
                    "request_id": r["id"],
                    "room_id": room_id,
                    "total": 688000,
                    "image_files_verified": len(photos["images"]),
                    "status": "awaiting_human_approval",
                    "approval_url": agreed["approval_url"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
