"""Export the API's OpenAPI schema to reference/openapi.json.

The committed spec is the API's contract; CI regenerates it and fails on drift
(see .github/workflows/api-quality.yml), so a route change that isn't reflected
in the spec is caught in review. Run after changing any endpoint:

    python -m scripts.export_openapi        # writes reference/openapi.json
    python -m scripts.export_openapi --check # exit 1 if the file is out of date
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from bass.api import ApiService, create_app
from bass.policy import PolicyEngine, default_policies
from bass.store import SQLiteStore
from bass.tools import default_registry

_OUT = Path(__file__).resolve().parent.parent / "openapi.json"


def _schema() -> dict:
    # A minimal service is enough — the OpenAPI schema derives from the route
    # definitions, not from registered workflows or a live datastore.
    store = SQLiteStore(str(Path(tempfile.mkdtemp()) / "openapi.db"))
    service = ApiService(store, {}, default_registry(),
                         PolicyEngine(default_policies()), jwt_secret="export")
    return create_app(service).openapi()


def _dump(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    rendered = _dump(_schema())
    if "--check" in args:
        current = _OUT.read_text() if _OUT.exists() else ""
        if current != rendered:
            print("openapi.json is out of date — run `python -m scripts.export_openapi`",
                  file=sys.stderr)
            return 1
        print("openapi.json is up to date")
        return 0
    _OUT.write_text(rendered)
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
