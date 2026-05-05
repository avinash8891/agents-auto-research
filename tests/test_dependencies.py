from __future__ import annotations

import re
from pathlib import Path


def test_numpy_is_pinned_to_numba_compatible_range() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = (repo_root / "pyproject.toml").read_text()
    match = re.search(r"(?ms)^dependencies = \[\n(?P<body>.*?)^\]", pyproject)

    assert match is not None
    dependencies = [
        line.strip().rstrip(",").strip('"')
        for line in match.group("body").splitlines()
        if line.strip().startswith('"')
    ]
    numpy_deps = [
        dep
        for dep in dependencies
        if dep == "numpy" or dep.startswith(("numpy<", "numpy>", "numpy=", "numpy!"))
    ]

    assert numpy_deps
    assert "numpy" not in numpy_deps
    assert any(_has_numba_compatible_numpy_bound(dep) for dep in numpy_deps)


def _has_numba_compatible_numpy_bound(dependency: str) -> bool:
    specs = dependency.removeprefix("numpy").split(",")
    return any(
        re.fullmatch(r"\s*<\s*2\.3(?:\.\d+)?\s*", spec)
        or re.fullmatch(r"\s*<=\s*2\.2(?:\.\d+)?\s*", spec)
        or re.fullmatch(r"\s*==\s*2\.[0-2](?:\.\d+)?\s*", spec)
        for spec in specs
    )
