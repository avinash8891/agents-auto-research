from __future__ import annotations

from typing import Any

from config_hash import _config_hash, _git_sha
from persistence_utils import utc_now_iso8601


def build_result_payload(
    strategy: str,
    config_path: str,
    config: dict[str, Any],
    file_paths: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "family": strategy,
        "config": config_path,
        "config_hash": _config_hash(config),
        "git_sha": _git_sha(),
        "timestamp": utc_now_iso8601(),
        "metrics_file": file_paths["metrics_file"],
        "trades_file": file_paths["trades_file"],
        "strategy_events_file": file_paths["strategy_events_file"],
        "diagnostics_file": file_paths["diagnostics_file"],
    }
    if config.get("data_provenance"):
        payload["data_provenance"] = config["data_provenance"]
    return payload
