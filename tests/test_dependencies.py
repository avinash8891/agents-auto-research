from __future__ import annotations

import tomllib
from pathlib import Path


def test_numpy_is_pinned_to_numba_compatible_range() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]

    numpy_deps = [dep for dep in dependencies if dep.startswith("numpy")]

    assert numpy_deps == ["numpy<2.3"]
