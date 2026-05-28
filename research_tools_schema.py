from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import Annotated

FINDING_TYPES = (
    "observation",
    "hypothesis",
    "validated_finding",
    "rejected_finding",
    "open_question",
    "implementation_note",
)

FINDING_STATUSES = ("unvalidated", "validated", "rejected", "stale")

RESULT_ORDER_VALUES = ("latest", "best", "worst")

FindingType = Literal[
    "observation",
    "hypothesis",
    "validated_finding",
    "rejected_finding",
    "open_question",
    "implementation_note",
]

FindingStatus = Literal["unvalidated", "validated", "rejected", "stale"]

ResultOrder = Literal["latest", "best", "worst"]

FindingTypeFilter = Literal[
    "",
    "observation",
    "hypothesis",
    "validated_finding",
    "rejected_finding",
    "open_question",
    "implementation_note",
]

NonEmptyStr = Annotated[str, Field(min_length=1)]


class AnalyzeTradesArgs(BaseModel):
    focus_question: NonEmptyStr


class WebSearchArgs(BaseModel):
    query: NonEmptyStr
    context: str = ""


class SaveFindingArgs(BaseModel):
    finding: NonEmptyStr
    finding_type: FindingType
    status: FindingStatus
    evidence: NonEmptyStr
    scope: NonEmptyStr
    expires_if: NonEmptyStr


class SearchFindingsArgs(BaseModel):
    query: NonEmptyStr
    finding_type: FindingTypeFilter = ""


class ListPastThesesArgs(BaseModel):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class GetPastThesisArgs(BaseModel):
    thesis_id: NonEmptyStr


class ListRoundResultsArgs(BaseModel):
    order: ResultOrder = "latest"
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=50)] = 10
    job_id: Optional[int] = None


class GetRoundResultArgs(BaseModel):
    research_round_id: NonEmptyStr
    detail: bool = False


class ListRejectionsArgs(BaseModel):
    round_number: Optional[Annotated[int, Field(ge=0)]] = None
    rejection_code: Optional[NonEmptyStr] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class GetRejectionArgs(BaseModel):
    round_number: Annotated[int, Field(ge=0)]
    thesis_id: NonEmptyStr


class RejectionPatternSummaryArgs(BaseModel):
    window_rounds: Annotated[int, Field(ge=1, le=50)] = 10


class MemoryStatusArgs(BaseModel):
    pass
