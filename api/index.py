"""
Vercel entrypoint for the VIMAAN API.

Vercel serves any `api/*.py` that exposes an ASGI `app` as a Python function,
so this file is a thin adapter: it puts `backend/` on the path and re-exports
the FastAPI application defined there. Keeping the real application under
backend/ means the package is still a normal Python project that runs with
uvicorn locally and imports cleanly in the test suite, rather than something
shaped around one host.

The database comes from DATABASE_URL, which the Neon integration sets on the
project. There is no SQLite file in a serverless deployment and there should
not be: the filesystem is ephemeral and every instance would diverge.
"""
from __future__ import annotations

import os
import sys

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from vimaan.api import app  # noqa: E402,F401  (re-exported for Vercel)
