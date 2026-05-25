"""Shared pytest fixtures for autoresearch tests.

Project rule G: real fixtures, not hand-written shapes. Fixtures here load
captured real values (config files, result.json schemas) from tests/fixtures
where possible, and use realistic production names (real family enums, real
status strings) elsewhere.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    """Install a test data-universe root for subprocess-based runtime tests."""
    data_root = Path(tempfile.mkdtemp(prefix="autoresearch-test-data-"))
    universes = data_root / "universes"
    universes.mkdir(parents=True, exist_ok=True)
    tiny_target = universes / "tiny_ema_data"
    tiny_source = FIXTURES_DIR / "tiny_ema_data"
    if not tiny_target.exists():
        tiny_target.symlink_to(tiny_source, target_is_directory=True)
    os.environ["AUTORESEARCH_DATA_ROOT"] = str(data_root)


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/ for tests that need to load real captures."""
    return FIXTURES_DIR


@pytest.fixture
def real_result_json(fixtures_dir: Path) -> dict:
    """Captured RESULT_JSON payload from a real backtest run."""
    return json.loads((fixtures_dir / "result_v1.json").read_text())


@pytest.fixture
def repo_root() -> Path:
    """The project root, useful for tests that load real configs."""
    return REPO_ROOT


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Keep integration tests honest: no pytest monkeypatch fixture there."""
    if item.get_closest_marker("integration") and "monkeypatch" in getattr(
        item, "fixturenames", ()
    ):
        raise RuntimeError("integration tests must not use the monkeypatch fixture")
