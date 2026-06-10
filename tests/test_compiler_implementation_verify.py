from __future__ import annotations

from compiler_implementation_verify import _verify_tests_cover_behavior


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


def test_tests_covering_behavior_accepts_asserting_test_function(tmp_path) -> None:
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

    failures = _verify_tests_cover_behavior(
        tmp_path,
        {"requires_code_change": True, "requested_primitives": ["new_gate"]},
        {"missing_primitives": ["new_gate"]},
    )

    assert failures == []
