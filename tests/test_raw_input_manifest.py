from __future__ import annotations

from pathlib import Path

import pytest

from raw_input_manifest import RawInputManifestError, available_raw_inputs


def test_available_raw_inputs_reads_manifest(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "raw_input_manifest.json"
    path.parent.mkdir()
    path.write_text('{"available_raw_inputs": ["ohlcv", "calendar"]}')

    assert available_raw_inputs(tmp_path) == frozenset({"ohlcv", "calendar"})


def test_available_raw_inputs_fails_loud_for_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(RawInputManifestError, match="runtime/raw_input_manifest.json"):
        available_raw_inputs(tmp_path)


def test_available_raw_inputs_fails_loud_for_bad_shape(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "raw_input_manifest.json"
    path.parent.mkdir()
    path.write_text('{"available_raw_inputs": "ohlcv"}')

    with pytest.raises(RawInputManifestError, match="available_raw_inputs"):
        available_raw_inputs(tmp_path)
