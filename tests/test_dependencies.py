from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 runtime path
    import tomli as tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


def test_numpy_is_pinned_to_numba_compatible_range() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(dep) for dep in pyproject["project"]["dependencies"]]
    numpy_reqs = [req for req in requirements if req.name == "numpy"]

    assert numpy_reqs
    assert all(str(req.specifier) for req in numpy_reqs)
    assert all(not req.specifier.contains(Version("2.3.0"), prereleases=True) for req in numpy_reqs)


def test_halo_engine_dependency_is_declared() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(dep) for dep in pyproject["project"]["dependencies"]]

    halo_reqs = [req for req in requirements if req.name == "halo-engine"]
    assert halo_reqs
    assert all(str(req.specifier) for req in halo_reqs)
