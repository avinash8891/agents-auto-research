"""Thesis compilation pipeline.

Facade for thesis compilation helpers extracted from research_subagent.py.
"""

from __future__ import annotations

from compiler_builder import build_missing_primitives as build_missing_primitives
from compiler_defaults import _get_orb_defaults as _get_orb_defaults
from compiler_operationalize import operationalize_thesis as operationalize_thesis
from compiler_operationalize import (
    thesis_needs_operationalization as thesis_needs_operationalization,
)
from compiler_research import compile_research_thesis as compile_research_thesis
from compiler_thesis_io import compile_config_thesis as compile_config_thesis
from compiler_thesis_io import compile_proposal_artifact as compile_proposal_artifact
from compiler_thesis_io import create_executable_artifact as create_executable_artifact
from compiler_thesis_io import derive_thesis_artifacts as derive_thesis_artifacts
from compiler_thesis_io import mark_request_completed as mark_request_completed
from compiler_thesis_io import write_research_artifact as write_research_artifact
from compiler_validate import validate_orb_runtime_config as validate_orb_runtime_config

__all__ = [
    "compile_research_thesis",
    "compile_proposal_artifact",
    "compile_config_thesis",
    "create_executable_artifact",
    "derive_thesis_artifacts",
    "write_research_artifact",
    "mark_request_completed",
    "_get_orb_defaults",
    "validate_orb_runtime_config",
    "thesis_needs_operationalization",
    "operationalize_thesis",
    "build_missing_primitives",
]
