"""
Source-file path resolution.

The assignment's source-of-truth files (policy_terms.json, test_cases.json,
sample_documents_guide.md, assignment.md) live at the repository root —
one level *above* multi_agent_claims_pipeline/ — not inside the project
directory as the originally documented tree assumed. A plain relative
path resolves against the process's CWD, which differs depending on
whether uvicorn/pytest is launched from
backend/ or the project root, and neither matches the actual file location.

`resolve_source_file` checks, in order: the path as given (CWD-relative,
so an explicit override still works), the project root, and the repo root
(parent of the project root) — returning the first that exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

# backend/app/config/paths.py -> parents[2] = backend/ -> parents[3] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REPO_ROOT = _PROJECT_ROOT.parent


def candidate_paths(filename: str) -> List[Path]:
    """All locations checked for `filename`, in priority order."""
    return [
        Path(filename),
        _PROJECT_ROOT / filename,
        _REPO_ROOT / filename,
    ]


def resolve_source_file(filename: str) -> Path:
    """
    Return the first existing path for `filename` among the candidate
    locations. Raises FileNotFoundError (with all tried locations listed)
    if none exist.
    """
    for candidate in candidate_paths(filename):
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(p) for p in candidate_paths(filename))
    raise FileNotFoundError(f"Could not find '{filename}'. Tried: {tried}")
