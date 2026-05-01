"""Tests for StrategyFamily variant-prefix helpers added in PR 5.

These pin the family-aware behavior that the planner will rely on after
the orb_/ema_ hardcoding is removed.
"""

from __future__ import annotations

import shlex
import sys
import sysconfig
from pathlib import Path

import backtest.legacy_entrypoint as legacy_entrypoint
import strategies.base as strategies_base
from backtest.legacy_entrypoint import strategy_for_script
from strategies import STRATEGIES
from strategy_family import StrategyFamily, load_family


def test_strategy_package_discovers_plugins_without_manual_strategy_imports() -> None:
    import strategies

    source = strategies.__loader__.get_source("strategies")
    assert "import strategies.ema" not in source
    assert "import strategies.orb" not in source
    assert "import strategies._demo" not in source
    assert {"_demo", "ema", "orb"}.issubset(STRATEGIES)


def test_orb_family_has_orb_prefix_and_default_variants() -> None:
    fam = load_family("orb")
    assert fam.variant_prefix == "orb_"
    assert "configs/variants/orb_spy_only.yaml" in fam.default_variants
    assert "configs/variants/orb_trailing_stop.yaml" in fam.default_variants
    assert fam.research_dirname == "orb-research-artifacts"


def test_orb_family_default_variants_are_shipped() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for rel_path in load_family("orb").default_variants:
        assert (repo_root / rel_path).is_file(), rel_path


def test_ema_family_has_ema_prefix_and_no_default_variants_yet() -> None:
    fam = load_family("ema")
    assert fam.variant_prefix == "ema_"
    assert fam.default_variants == ()


def test_strategy_family_exposes_research_description() -> None:
    assert "OPENING RANGE BREAKOUT (ORB) STRATEGY" in load_family("orb").description_for_research
    assert "5 EMA PULLBACK/REVERSAL STRATEGY" in load_family("ema").description_for_research


def test_orb_family_is_built_from_registered_strategy_metadata() -> None:
    assert "orb" in STRATEGIES
    strategy = STRATEGIES["orb"]
    family = load_family("orb")

    assert family.benchmark_script == strategy.benchmark_script
    assert family.vps_benchmark_script == strategy.vps_benchmark_script
    assert family.description_for_research == strategy.description_for_research
    assert family.proposals_dirname == strategy.family_dirnames.proposals
    assert family.compilations_dirname == strategy.family_dirnames.compilations
    assert family.contracts_dirname == strategy.family_dirnames.contracts
    assert family.run_queue_dirname == strategy.family_dirnames.run_queue
    assert family.research_dirname == strategy.family_dirnames.research
    assert family.builder_requests_dirname == strategy.family_dirnames.builder_requests
    assert family.base_config_filename == strategy.family_dirnames.base_config_filename
    assert family.runs_dirname == strategy.family_dirnames.runs
    assert family.variant_prefix == strategy.family_dirnames.variant_prefix


def test_strategy_family_has_no_embedded_orb_description() -> None:
    source = __import__("strategy_family").__loader__.get_source("strategy_family")
    assert "_ORB_DESCRIPTION_FOR_RESEARCH" not in source


def test_baseline_config_path_uses_base_config_filename() -> None:
    assert load_family("orb").baseline_config_path == "configs/orb_base.yaml"
    assert load_family("ema").baseline_config_path == "configs/ema_base.yaml"


def test_variant_config_path_uses_family_prefix() -> None:
    assert (
        load_family("orb").variant_config_path("spy_only") == "configs/variants/orb_spy_only.yaml"
    )
    assert (
        load_family("ema").variant_config_path("aggressive")
        == "configs/variants/ema_aggressive.yaml"
    )


def test_slug_from_config_strips_family_prefix() -> None:
    orb = load_family("orb")
    assert orb.slug_from_config("configs/variants/orb_spy_only.yaml") == "spy_only"
    # Non-prefixed input passes through unchanged.
    assert orb.slug_from_config("configs/variants/something_else.yaml") == "something_else"

    ema = load_family("ema")
    assert ema.slug_from_config("configs/variants/ema_aggressive.yaml") == "aggressive"
    # ema family does NOT strip orb_ — that's a different family's prefix.
    assert ema.slug_from_config("configs/variants/orb_spy_only.yaml") == "orb_spy_only"


def test_strategy_family_default_variant_prefix_is_not_strategy_specific() -> None:
    """A StrategyFamily constructed with only required fields must have a
    neutral default variant_prefix; concrete families get prefixes from plugins."""
    fam = StrategyFamily(name="x", benchmark_script="x.py")
    assert fam.variant_prefix == ""
    assert fam.default_variants == ()


def test_benchmark_command_uses_generic_strategy_runner() -> None:
    config_path = "configs/ema base.yaml"
    output_dir = "/tmp/run dir"
    command = load_family("ema").benchmark_command(config_path, output_dir=output_dir)
    assert command == (
        "python3 -m backtest.runner --strategy ema --config "
        f"{shlex.quote(config_path)} --output-dir {shlex.quote(output_dir)}"
    )
    assert "backtest_5ema.py" not in command


def test_legacy_backtest_scripts_resolve_strategy_from_registry() -> None:
    assert strategy_for_script("backtest_5ema.py") == "ema"
    assert strategy_for_script("backtest_orb_v2.py") == "orb"


def test_legacy_entrypoint_reexecs_current_interpreter(monkeypatch) -> None:
    captured = {}

    def fake_execv(executable: str, argv: list[str]) -> None:
        captured["executable"] = executable
        captured["argv"] = argv
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(legacy_entrypoint.os, "execv", fake_execv)
    monkeypatch.setattr(sys, "argv", ["backtest_5ema.py", "--config", "cfg.json"])

    try:
        legacy_entrypoint.main_for_script("backtest_5ema.py")
    except RuntimeError as exc:
        assert str(exc) == "exec intercepted"

    assert captured["executable"] == sys.executable
    assert captured["argv"][0] == sys.executable
    assert captured["argv"][1:4] == ["-m", "backtest.runner", "--strategy"]
    assert captured["argv"][4] == "ema"


def test_registered_strategies_do_not_keep_duplicate_default_yaml_files() -> None:
    """Strategy defaults have one canonical YAML source per family."""
    repo_root = Path(__file__).resolve().parents[1]
    for family_name, strategy in STRATEGIES.items():
        config_default = repo_root / "configs" / strategy.family_dirnames.base_config_filename
        package_default = repo_root / "strategies" / family_name / "defaults.yaml"
        assert config_default.exists(), f"{family_name} missing canonical {config_default}"
        assert (
            not package_default.exists()
        ), f"{family_name} has duplicate defaults at {package_default}"


def test_load_strategy_defaults_falls_back_to_installed_data_files(
    monkeypatch, tmp_path: Path
) -> None:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "orb_base.yaml").write_text("validation_start: '2024-01-01'\n")

    monkeypatch.setattr(
        strategies_base,
        "__file__",
        str(tmp_path / "site-packages" / "fake.py"),
    )
    monkeypatch.setattr(sysconfig, "get_paths", lambda: {"data": str(tmp_path)})

    assert strategies_base.load_strategy_defaults("orb") == {"validation_start": "2024-01-01"}
