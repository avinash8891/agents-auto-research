"""Tests for StructuredRejection — the machine-readable rejection record."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from research_types import StructuredRejection


def _valid_payload() -> dict:
    return {
        "rejected_at": "2026-05-09T13:45:22Z",
        "round": 3,
        "thesis_id": "ema-rsi-filter-v1",
        "stage": "stage_1",
        "rejection_code": "config_key_overlap_real",
        "rule_violated": "config-key Jaccard >= 0.5",
        "evidence": {
            "shared_keys": ["min_stop_distance_pct"],
            "overlap_with_thesis": "tighten_risk_structure_via_min_stop_distance_pct_floor",
            "overlap_pct": 100,
        },
        "remediation_hint": (
            "this lever was tested in round 2; propose from a different mechanism dimension"
        ),
        "validator_version": "2.3.0",
    }


def test_structured_rejection_round_trips() -> None:
    payload = _valid_payload()
    obj = StructuredRejection.model_validate(payload)

    assert obj.rejection_code == "config_key_overlap_real"
    assert obj.evidence["shared_keys"] == ["min_stop_distance_pct"]

    dumped = obj.model_dump(mode="json")
    rebuilt = StructuredRejection.model_validate(dumped)
    assert rebuilt == obj


def test_structured_rejection_serializes_to_json() -> None:
    obj = StructuredRejection.model_validate(_valid_payload())
    text = obj.model_dump_json()
    parsed = json.loads(text)
    assert parsed["thesis_id"] == "ema-rsi-filter-v1"
    assert parsed["stage"] == "stage_1"


def test_structured_rejection_rejects_invalid_stage() -> None:
    payload = _valid_payload()
    payload["stage"] = "stage_42"
    with pytest.raises(ValidationError, match="stage"):
        StructuredRejection.model_validate(payload)


def test_structured_rejection_requires_core_fields() -> None:
    for missing_key in ("round", "thesis_id", "stage", "rejection_code"):
        payload = _valid_payload()
        del payload[missing_key]
        with pytest.raises(ValidationError):
            StructuredRejection.model_validate(payload)


def test_structured_rejection_evidence_defaults_to_empty_dict() -> None:
    payload = _valid_payload()
    del payload["evidence"]
    obj = StructuredRejection.model_validate(payload)
    assert obj.evidence == {}


def test_structured_rejection_accepts_compile_stage() -> None:
    payload = _valid_payload()
    payload["stage"] = "compile"
    obj = StructuredRejection.model_validate(payload)
    assert obj.stage == "compile"


def test_structured_rejection_accepts_stage_2() -> None:
    payload = _valid_payload()
    payload["stage"] = "stage_2"
    obj = StructuredRejection.model_validate(payload)
    assert obj.stage == "stage_2"
