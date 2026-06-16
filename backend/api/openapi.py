from __future__ import annotations

import json
from pathlib import Path

from api.main import create_app
from config.settings import Settings


def main() -> None:
    settings = Settings(secret_key="q6boIR1bNUZ-gozCYInhKglccJM7x11ysXmhquzIoUQ=")
    app = create_app(settings=settings)
    output_path = Path("frontend/src/api/openapi.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
