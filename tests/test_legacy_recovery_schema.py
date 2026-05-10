"""Tests for L2: schema additions recovering legacy prompt requirements.

Adds Alternative, EvidenceCitation, source_code_verification.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_types import (
    Alternative,
    EvidenceCitation,
    ResearchThesis,
)


def test_alternative_model_requires_mechanism_and_why_rejected() -> None:
    obj = Alternative(
        mechanism="opening drive directional gate",
        why_rejected="evidence weaker than chosen mechanism on selected splits",
    )
    assert obj.mechanism.startswith("opening")


def test_alternative_model_rejects_short_why_rejected() -> None:
    with pytest.raises(ValidationError):
        Alternative(mechanism="x", why_rejected="too short")


def test_evidence_citation_model_requires_source_and_citation() -> None:
    obj = EvidenceCitation(
        source="web_search",
        citation="Gao Han Li Zhou (2015): first half-hour return predicts last half-hour with OOS R^2 ~1.7%",
    )
    assert obj.source == "web_search"


def test_evidence_citation_rejects_invalid_source() -> None:
    with pytest.raises(ValidationError):
        EvidenceCitation(source="random_source", citation="x")


def test_evidence_citation_accepts_known_sources() -> None:
    for source in ("web_search", "analyst", "source_code", "experiment_result", "memory"):
        EvidenceCitation(source=source, citation="data")


def _base_thesis_dict(thesis_id: str = "t_alt") -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Filter setups by minimum opening volatility to avoid noise.",
        "mechanism": "Low-volatility opens have weaker microstructure signals.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": "Volatility floor on alert candle, different lever family.",
        "config_changes": {"alert_min_atr_pct": 0.001},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups follow through more reliably.",
            }
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_volatility_separation",
                "condition": "If volatility quintile PF spread is below 0.2, the mechanism does not hold.",
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
    }


def test_research_thesis_has_alternatives_considered_default_empty() -> None:
    obj = ResearchThesis.model_validate(_base_thesis_dict())
    assert obj.alternatives_considered == []


def test_research_thesis_accepts_alternatives_considered() -> None:
    payload = _base_thesis_dict()
    payload["alternatives_considered"] = [
        {
            "mechanism": "opening drive directional gate",
            "why_rejected": "weaker disconfirmer than the volatility floor mechanism",
        },
        {
            "mechanism": "RVOL gate on stocks-in-play list",
            "why_rejected": "requires data we don't have for the train period",
        },
    ]
    obj = ResearchThesis.model_validate(payload)
    assert len(obj.alternatives_considered) == 2


def test_research_thesis_has_evidence_citations_default_empty() -> None:
    obj = ResearchThesis.model_validate(_base_thesis_dict())
    assert obj.evidence_citations == []


def test_research_thesis_accepts_evidence_citations() -> None:
    payload = _base_thesis_dict()
    payload["evidence_citations"] = [
        {"source": "web_search", "citation": "Cont et al on order-flow imbalance"},
        {"source": "analyst", "citation": "round-3 analyst: Q1 PF 1.7 vs Q5 PF 0.05"},
    ]
    obj = ResearchThesis.model_validate(payload)
    assert obj.evidence_citations[0].source == "web_search"
    assert obj.evidence_citations[1].source == "analyst"


def test_research_thesis_has_source_code_verification_default_empty() -> None:
    obj = ResearchThesis.model_validate(_base_thesis_dict())
    assert obj.source_code_verification == ""


def test_research_thesis_accepts_source_code_verification() -> None:
    payload = _base_thesis_dict()
    payload["source_code_verification"] = (
        "strategies/ema/signals.py:detect_pullback uses min_stop_distance_pct as a hard floor "
        "in the entry filter; widening this would increase tradeable setups."
    )
    obj = ResearchThesis.model_validate(payload)
    assert obj.source_code_verification.startswith("strategies/ema")
