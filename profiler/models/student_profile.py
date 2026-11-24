from datetime import datetime, timezone

from pydantic import BaseModel, Field

from profiler.models.skill_state import SkillState


class StudentProfile(BaseModel):
    user_id: str
    skills: dict[str, SkillState] = Field(default_factory=dict)
    last_grade: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime
