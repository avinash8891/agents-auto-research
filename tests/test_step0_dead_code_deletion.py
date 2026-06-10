from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


DELETED_SYMBOLS = (
    "_check" + "_neighboring_threshold",
    "_validate_expected_effects" + "_metrics_backed",
    "generate" + "_variants",
    "_NUMERIC" + "_VARIANT_BOUNDS",
    "_validate" + "_structural",
    "_validate" + "_thesis_quality",
    "_validate" + "_config_validity",
    "_runtime" + "_code_text",
    "_active_string" + "_token_present",
)


DELETED_FILES = (
    "compiler" + "_validate.py",
    "orb_experiment" + "_schema.py",
)


def test_step0_deleted_symbols_do_not_remain_in_live_source() -> None:
    live_files = [
        ROOT / "thesis_validator.py",
        ROOT / "compiler_implementation_verify.py",
        ROOT / "scripts" / "check_prompt_drift.py",
        ROOT / "tests" / "test_validator_subsections.py",
        ROOT / "tests" / "test_stage1_rules.py",
        ROOT / "tests" / "test_validator_gate_coverage.py",
        ROOT / "tests" / "test_thesis_validator.py",
    ]

    remaining: list[str] = []
    for path in live_files:
        text = path.read_text()
        for symbol in DELETED_SYMBOLS:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text):
                remaining.append(f"{path.relative_to(ROOT)}:{symbol}")

    assert remaining == []


def test_step0_deleted_files_and_dead_orb_validator_are_absent() -> None:
    remaining = [path for path in DELETED_FILES if (ROOT / path).exists()]
    schema_text = (ROOT / "strategies" / "orb" / "schema.py").read_text()

    assert remaining == []
    assert "def validate_runtime_config(" not in schema_text
