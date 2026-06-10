from __future__ import annotations

import subprocess

from compiler_implementation_verify import _verify_tests_cover_behavior


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit_all(root) -> None:
    _git("init", cwd=root)
    _git("config", "user.email", "test@example.invalid", cwd=root)
    _git("config", "user.name", "Test User", cwd=root)
    _git("add", ".", cwd=root)
    _git("commit", "-m", "baseline", cwd=root)


def test_tests_covering_behavior_ignores_incidental_token_strings(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_existing.py").write_text(
        """
def helper():
    return {"new_gate": True}
""",
        encoding="utf-8",
    )

    failures = _verify_tests_cover_behavior(
        tmp_path,
        {"requires_code_change": True, "requested_primitives": ["new_gate"]},
        {"missing_primitives": ["new_gate"]},
    )

    assert failures == ["tests_covering_behavior_missing:new_gate"]


def test_tests_covering_behavior_rejects_preexisting_asserting_test_function(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_new_gate.py").write_text(
        """
def test_new_gate_changes_behavior():
    result = {"new_gate": True}
    assert result["new_gate"] is True
""",
        encoding="utf-8",
    )
    _commit_all(tmp_path)

    failures = _verify_tests_cover_behavior(
        tmp_path,
        {"requires_code_change": True, "requested_primitives": ["new_gate"]},
        {"missing_primitives": ["new_gate"]},
    )

    assert failures == ["tests_covering_behavior_missing:new_gate"]


def test_tests_covering_behavior_accepts_new_asserting_test_function(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_existing.py").write_text(
        """
def test_existing_behavior():
    result = {"old_gate": True}
    assert result["old_gate"] is True
""",
        encoding="utf-8",
    )
    _commit_all(tmp_path)
    (tests_dir / "test_new_gate.py").write_text(
        """
def test_new_gate_changes_behavior():
    result = {"new_gate": True}
    assert result["new_gate"] is True
""",
        encoding="utf-8",
    )

    failures = _verify_tests_cover_behavior(
        tmp_path,
        {"requires_code_change": True, "requested_primitives": ["new_gate"]},
        {"missing_primitives": ["new_gate"]},
    )

    assert failures == []


def test_tests_covering_behavior_accepts_modified_asserting_test_function(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    path = tests_dir / "test_existing.py"
    path.write_text(
        """
def test_existing_behavior():
    result = {"old_gate": True}
    assert result["old_gate"] is True
""",
        encoding="utf-8",
    )
    _commit_all(tmp_path)
    path.write_text(
        """
def test_existing_behavior():
    result = {"new_gate": True}
    assert result["new_gate"] is True
""",
        encoding="utf-8",
    )

    failures = _verify_tests_cover_behavior(
        tmp_path,
        {"requires_code_change": True, "requested_primitives": ["new_gate"]},
        {"missing_primitives": ["new_gate"]},
    )

    assert failures == []


def test_tests_covering_behavior_accepts_committed_new_asserting_test_function(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_existing.py").write_text(
        """
def test_existing_behavior():
    result = {"old_gate": True}
    assert result["old_gate"] is True
""",
        encoding="utf-8",
    )
    _commit_all(tmp_path)
    (tests_dir / "test_new_gate.py").write_text(
        """
def test_new_gate_changes_behavior():
    result = {"new_gate": True}
    assert result["new_gate"] is True
""",
        encoding="utf-8",
    )
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-m", "add new gate test", cwd=tmp_path)

    failures = _verify_tests_cover_behavior(
        tmp_path,
        {"requires_code_change": True, "requested_primitives": ["new_gate"]},
        {"missing_primitives": ["new_gate"]},
    )

    assert failures == []
