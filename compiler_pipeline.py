"""Thesis compilation pipeline.

Converts raw research theses into executable config artifacts:
  1. Operationalization — resolves ambiguous terms into exact contracts
  2. Compilation — contract → runtime config via family-specific compiler
  3. Artifact creation — writes proposal, compilation, queue files
  4. Builder dispatch — implements missing primitives via CLI

Migrated from research_subagent.py — all thesis-to-config logic lives here.
"""

from __future__ import annotations

from compiler_builder import build_missing_primitives
from compiler_defaults import _get_ema_defaults as _get_ema_defaults
from compiler_defaults import _get_orb_defaults as _get_orb_defaults
from compiler_legacy import compile_ema_thesis as compile_ema_thesis
from compiler_legacy import compile_proposal_artifact as compile_proposal_artifact
from compiler_legacy import create_executable_artifact as create_executable_artifact
from compiler_legacy import derive_thesis_artifacts as derive_thesis_artifacts
from compiler_legacy import mark_request_completed as mark_request_completed
from compiler_legacy import write_research_artifact as write_research_artifact
from compiler_operationalize import operationalize_thesis as operationalize_thesis
from compiler_operationalize import (
    thesis_needs_operationalization as thesis_needs_operationalization,
)
from compiler_research import compile_research_thesis as compile_research_thesis
from compiler_validate import _config_content_hash as _config_content_hash
from compiler_validate import validate_ema_runtime_config as validate_ema_runtime_config
from compiler_validate import validate_orb_runtime_config as validate_orb_runtime_config

build_missing_primitives = build_missing_primitives


# ---------------------------------------------------------------------------
# Research thesis → ExperimentContract (new structured path)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Legacy artifact creation (kept for ORB compatibility)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Builder dispatch: implements missing primitives via CLI
# ---------------------------------------------------------------------------
