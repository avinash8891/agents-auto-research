"""Thesis compilation pipeline.

Facade for thesis compilation helpers extracted from research_subagent.py.
"""

from __future__ import annotations

from compiler_builder import build_missing_primitives as build_missing_primitives
from compiler_operationalize import (
    thesis_needs_operationalization as thesis_needs_operationalization,
)
from compiler_research import compile_research_thesis as compile_research_thesis

__all__ = [
    "compile_research_thesis",
    "thesis_needs_operationalization",
    "build_missing_primitives",
]
