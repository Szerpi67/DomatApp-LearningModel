from datetime import datetime

from pydantic import BaseModel, Field

from ..core import irt_model

class SubskillState(BaseModel):
    ability: float = 0.0
    uncertainty: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    observations: int = Field(default=0, ge=0)
    working_difficulty: int | None = Field(default=None, ge=1, le=10)
    last_practiced_at: datetime

class SkillState(BaseModel):
    skill_id: str
    ability: float = 0.0
    uncertainty: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    observations: int = Field(default=0, ge=0)
    working_difficulty: int | None = Field(default=None, ge=1, le=10)
    last_practiced_at: datetime
    subskills: dict[str, SubskillState] = Field(default_factory=dict)



