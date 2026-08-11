"""app package"""

# Every module under `app/` imports its siblings with absolute imports
# (`from app.config.settings import ...`, etc.), which only resolve if
# `backend/` itself is on sys.path. That's automatic when the process is
# launched from `backend/` (e.g. local dev: `cd backend && uvicorn
# app.main:app`), but not when launched as `backend.app.main:app` from the
# repository root (e.g. a Render start command) — in that case only the
# repo root is on sys.path, not `backend/`. Adding it here, at package
# import time, makes both launch styles work without needing an extra
# PYTHONPATH env var. No-op if `backend/` is already on sys.path.
import sys as _sys
from pathlib import Path as _Path

_backend_dir = str(_Path(__file__).resolve().parent.parent)
if _backend_dir not in _sys.path:
    _sys.path.insert(0, _backend_dir)
