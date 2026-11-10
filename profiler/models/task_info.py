from pydantic import BaseModel, Field
from enum import Enum

from pydantic import BaseModel, Field, model_validator

class TaskType(str, Enum):
    OPEN = "OPEN"
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"

class TaskSkill(BaseModel):
    skill_id: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1, allow_inf_nan=False)
    subskill_ids: list[str] = Field(default_factory=list)

class TaskInfo(BaseModel):
    type: TaskType
    difficulty: int = Field(ge=1, le=10)
    primary_skill_id: str
    skills: list[TaskSkill] = Field(min_length=1)
    task_template_id: str | None = None

    @model_validator(mode="after")
    def validate_skills(self):
        skill_ids = [skill.skill_id for skill in self.skills]

        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("Duplicate skill IDs are not allowed")

        total_weight = sum(skill.weight for skill in self.skills)

        if abs(total_weight - 1.0) > 1e-6:
            raise ValueError("Skill weights must sum to 1.0")

        return self


