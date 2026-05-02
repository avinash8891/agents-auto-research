from __future__ import annotations

from pathlib import Path


def test_cubic_review_pre_push_hook_bootstraps_cubic_path() -> None:
    config_text = Path(".pre-commit-config.yaml").read_text()
    script_text = Path("scripts/cubic_review.sh").read_text()

    assert "cubic-review" in config_text
    assert "entry: scripts/cubic_review.sh origin/main" in config_text
    assert 'timeout_seconds="${CUBIC_REVIEW_TIMEOUT_SECONDS:-300}"' in script_text
    assert (
        'env PATH="$HOME/.cubic/bin:$PATH" cubic review --print-logs --base "$base_ref" "$@"'
        in script_text
    )
