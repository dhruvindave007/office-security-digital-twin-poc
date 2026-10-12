from pathlib import Path
from typing import Optional


def _resolve_data_path(target_path: Optional[str], default_relative: str) -> Path:
    """Resolve directory or file path across different execution working directories."""
    path_to_find = target_path if target_path is not None else default_relative
    candidate = Path(path_to_find)
    if candidate.exists():
        return candidate

    # Look relative to project root or parent directories
    current = Path.cwd()
    for base in [current, current / "backend", current.parent]:
        p = base / path_to_find
        if p.exists():
            return p

    # Try relative to this file's directory
    seed_dir = Path(__file__).resolve().parent
    for ancestor in [seed_dir, seed_dir.parent, seed_dir.parent.parent]:
        p = ancestor / path_to_find
        if p.exists():
            return p

    return candidate
