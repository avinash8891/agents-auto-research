from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from artifact_io import write_json_artifact

RoundStatus = Literal["completed", "running", "failed"]


class RoundArtifact(BaseModel):
    """Canonical payload stored in each research round's round.json."""

    model_config = ConfigDict(extra="ignore")

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
    findings: list[str] = Field(default_factory=list)
    run_id: str | None = None
    created_at: str = ""
    usage: dict[str, Any] | None = None

    @field_validator("job_id", mode="before")
    @classmethod
    def _legacy_job_alias(cls, value: Any, info: Any) -> Any:
        if value is not None:
            return value
        data = getattr(info, "data", {}) or {}
        return data.get("job")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RoundArtifact":
        normalized = dict(payload)
        if "job_id" not in normalized and "job" in normalized:
            normalized["job_id"] = normalized["job"]
        if "selected_thesis_id" not in normalized and "thesis_id" in normalized:
            normalized["selected_thesis_id"] = normalized["thesis_id"]
        if not normalized.get("status") and normalized.get("outcome"):
            normalized["status"] = "completed"
        generated_configs = normalized.get("generated_configs")
        generated_config_path = normalized.get("generated_config_path") or ""
        if generated_configs is None:
            normalized["generated_configs"] = []
        if generated_config_path and not normalized.get("generated_configs"):
            normalized["generated_configs"] = []
        return cls.model_validate(normalized)

    def to_payload(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        if self.selected_thesis_id:
            payload["thesis_id"] = self.selected_thesis_id
        return payload


def read_round_artifact(path: Path) -> RoundArtifact:
    import json

    payload = json.loads(path.read_text())
    if "round_number" not in payload:
        raw_round = path.parent.name.removeprefix("round-")
        if raw_round == "0-baseline":
            payload["round_number"] = 0
        else:
            payload["round_number"] = raw_round.split("-", 1)[0]
    return RoundArtifact.from_payload(payload)


def write_round_artifact(path: Path, artifact: RoundArtifact) -> None:
    write_json_artifact(path, artifact.to_payload())
