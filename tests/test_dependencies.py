from __future__ import annotations

from pathlib import Path


def test_numpy_is_pinned_to_numba_compatible_range() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = (repo_root / "pyproject.toml").read_text()

    assert '"numpy<2.3"' in pyproject
    assert '"numpy",' not in pyproject
