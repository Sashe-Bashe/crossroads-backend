import json
from pathlib import Path

from market.main import app

Path("docs/openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
print("Updated docs/openapi.json")
