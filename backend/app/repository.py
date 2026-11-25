from __future__ import annotations

import os

from .config import load_local_env
from .models import RosterInput
from .sample_data import demo_data


def load_roster_input() -> RosterInput:
    """Uses seeded data by default so the application is demonstrable without secrets.

    Replace this adapter after mapping the actual Supabase table names. The public client
    key is sufficient for reads when Row Level Security policies allow the queries.
    """
    load_local_env()
    if os.getenv("DATA_SOURCE", "sample").lower() != "supabase":
        return demo_data()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY are required when DATA_SOURCE=supabase.")
    from .supabase_repository import data_readiness

    readiness = data_readiness()
    raise RuntimeError("Live roster generation is unavailable. " + " ".join(readiness["blockers"]))
