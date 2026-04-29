"""Shared pytest fixtures for autoresearch tests.

Project rule G: real fixtures, not hand-written shapes. Fixtures here load
captured real values (config files, result.json schemas) from tests/fixtures
where possible, and use realistic production names (real family enums, real
status strings) elsewhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/ for tests that need to load real captures."""
    return FIXTURES_DIR


@pytest.fixture
def real_result_json(fixtures_dir: Path) -> dict:
    """Captured RESULT_JSON payload from a real backtest run."""
    return json.loads((fixtures_dir / "result_v1.json").read_text())


@pytest.fixture
def real_ideas_backlog_path(fixtures_dir: Path) -> Path:
    """Path to a captured ideas-backlog markdown."""
    return fixtures_dir / "ideas_backlog.md"


@pytest.fixture
def repo_root() -> Path:
    """The project root, useful for tests that load real configs."""
    return REPO_ROOT
