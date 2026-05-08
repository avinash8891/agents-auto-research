from __future__ import annotations

from typing import Any

from trace_sdk import record_event


def _emit_adapter_event(
    adapter_name: str,
    *,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
) -> dict[str, Any]:
    return record_event(
        source_module=f"trace_adapters.{adapter_name}",
        category=adapter_name,
        action=action,
        summary=summary,
        payload=payload,
        artifact_paths=artifact_paths,
    )


from trace_adapters.halo import emit_halo_event
from trace_adapters.recursive_improve import emit_recursive_improve_event
from trace_adapters.reflexio import emit_reflexio_event

__all__ = [
    "emit_halo_event",
    "emit_recursive_improve_event",
    "emit_reflexio_event",
]
