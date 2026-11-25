from __future__ import annotations

import json
import sys

from app.supabase_inspector import inspect_supabase_schema


if __name__ == "__main__":
    try:
        print(json.dumps(inspect_supabase_schema(), indent=2, default=str))
    except RuntimeError as error:
        print(f"Supabase inspection could not start: {error}", file=sys.stderr)
        raise SystemExit(1)
