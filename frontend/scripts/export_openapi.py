"""Export the existing app contract without opening database connections.

Run from the repository root: python frontend/scripts/export_openapi.py
"""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from backend.app import create_app

(root / "frontend/openapi.json").write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
print("Exported existing OpenAPI contract; no database access")
