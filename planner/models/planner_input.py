from typing import Optional
from pydantic import BaseModel, Field, model_validator
from profiler.models.student_profile import StudentProfile

class CurriculumContext(BaseModel):
    grade: int = Field(..., description="Klasa, np. 4 lub 8")
    education_stage: str = Field(..., description="np. 'szkoła podstawowa' lub 'liceum'")
    curriculum_level: str = Field(default="podstawa", description="np. 'podstawa' lub 'rozszerzenie'")
    curriculum_version: Optional[str] = Field(default=None, description="Wersja programu nauczania")

class PlannerInput(BaseModel):
    user_id: str
    target_skill_id: Optional[str] = Field(
        default=None,
        description="Aktywny cel nauki; pomiń, aby Planner wybrał cel z działu.",
    )
    selected_domain: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Wybrany przez ucznia dział, np. fractions.",
    )
    student_profile: StudentProfile
    curriculum_context: CurriculumContext

    @model_validator(mode="after")
    def validate_learning_focus(self):
        if not self.target_skill_id and not self.selected_domain:
            raise ValueError("Provide a target skill or a selected learning domain")
        if self.target_skill_id and self.selected_domain:
            raise ValueError("Provide either a target skill or a learning domain")
        return self
