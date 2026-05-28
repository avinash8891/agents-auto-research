from __future__ import annotations

import pytest

from autoresearch_runtime_paths import research_round_id


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
