from __future__ import annotations

import pytest

from autoresearch_runtime_paths import research_round_id, research_round_id_or_empty


class TestResearchRoundId:
    def test_canonical_format(self) -> None:
        assert research_round_id(12, 5) == "job-12-round-5"

    def test_baseline_round_zero_allowed(self) -> None:
        assert research_round_id(1, 0) == "job-1-round-0"

    def test_zero_job_rejected(self) -> None:
        with pytest.raises(ValueError, match="job id must be >= 1"):
            research_round_id(0, 1)

    def test_negative_round_rejected(self) -> None:
        with pytest.raises(ValueError, match="round number must be >= 0"):
            research_round_id(1, -1)


class TestResearchRoundIdOrEmpty:
    def test_valid_inputs_return_helper_result(self) -> None:
        assert research_round_id_or_empty(7, 3) == "job-7-round-3"

    def test_job_zero_returns_empty(self) -> None:
        assert research_round_id_or_empty(0, 1) == ""

    def test_negative_round_returns_empty(self) -> None:
        assert research_round_id_or_empty(1, -1) == ""

    def test_non_int_job_returns_empty(self) -> None:
        assert research_round_id_or_empty("not-a-number", 1) == ""

    def test_none_job_returns_empty(self) -> None:
        assert research_round_id_or_empty(None, 1) == ""
