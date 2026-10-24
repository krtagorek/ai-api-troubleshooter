"""Local health check and FastAPI entry point."""

import json

from backend.app import create_app

app = create_app()


def health_check() -> dict[str, str]:
    """Report application availability without contacting external services."""
    return {"status": "ok", "service": "ai-api-troubleshooter"}


if __name__ == "__main__":
    print(json.dumps(health_check()))
