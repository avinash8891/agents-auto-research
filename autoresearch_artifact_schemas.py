from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from artifact_io import write_json_artifact

RoundStatus = Literal["completed", "running", "failed"]


class RoundArtifact(BaseModel):
    """Canonical payload stored in each research round's round.json."""

    model_config = ConfigDict(extra="forbid", strict=True)

    job_id: int = Field(ge=1)
    round_number: int = Field(ge=0)
    strategy_family: str = ""
    status: RoundStatus = "completed"
    outcome: str = ""
    selected_thesis_id: str = ""
    generated_configs: list[str] = Field(default_factory=list)
    generated_config_path: str = ""
    new_theses_generated: int = Field(default=0, ge=0)
    suggested_theses: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""
    usage: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RoundArtifact":
        return cls.model_validate(payload)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def read_round_artifact(path: Path) -> RoundArtifact:
    import json

    payload = json.loads(path.read_text())
    return RoundArtifact.from_payload(payload)


def write_round_artifact(path: Path, artifact: RoundArtifact) -> None:
    write_json_artifact(path, artifact.to_payload())
