
from enum import Enum

from pydantic import BaseModel


class LearningPurpose(str, Enum):
    DIAGNOSTIC = "DIAGNOSTIC"
    PRACTICE = "PRACTICE"
    REVIEW = "REVIEW"
    ASSESSMENT = "ASSESSMENT"


class LearningContext(BaseModel):
    purpose: LearningPurpose
    learning_method: str | None = None
    learning_block_id: str | None = None
    plan_id: str | None = None
