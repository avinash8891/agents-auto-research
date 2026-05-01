from __future__ import annotations

from pathlib import Path


def test_cubic_review_pre_push_hook_bootstraps_cubic_path() -> None:
    config_text = Path(".pre-commit-config.yaml").read_text()

    assert "cubic-review" in config_text
    assert 'PATH="$HOME/.cubic/bin:$PATH" exec cubic review --base origin/main' in config_text
