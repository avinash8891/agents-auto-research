"""Thesis compilation pipeline.

Facade for thesis compilation helpers extracted from research_subagent.py.
"""

from __future__ import annotations

from compiler_builder import build_missing_primitives as build_missing_primitives
from compiler_operationalize import operationalize_thesis as operationalize_thesis
from compiler_operationalize import (
    thesis_needs_operationalization as thesis_needs_operationalization,
)
from compiler_research import compile_research_thesis as compile_research_thesis
from compiler_thesis_io import mark_request_completed as mark_request_completed
from compiler_thesis_io import write_research_artifact as write_research_artifact

__all__ = [
    "compile_research_thesis",
    "write_research_artifact",
    "mark_request_completed",
    "thesis_needs_operationalization",
    "operationalize_thesis",
    "build_missing_primitives",
]
