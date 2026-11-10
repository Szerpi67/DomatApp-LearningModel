from typing import Literal

from pydantic import BaseModel, Field, model_validator

from planner.models.learning_plan import PlanItem
from planner.models.planner_input import CurriculumContext


class TaskGenerationRequest(BaseModel):
    plan_id: str = Field(min_length=1)
    plan_item: PlanItem
    curriculum_context: CurriculumContext


class AnswerOption(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class TaskTemplate(BaseModel):
    schema_version: Literal["task.v0"] = "task.v0"
    task_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    task_type: Literal["closed_single_choice"] = "closed_single_choice"
    skill_id: str = Field(min_length=1)
    skill_ids: list[str] = Field(default_factory=list)
    subskill_ids: list[str] = Field(default_factory=list)
    subskills_by_skill: dict[str, list[str]] = Field(default_factory=dict)
    difficulty: int = Field(ge=1, le=10)
    requested_difficulty: float = Field(ge=1.0, le=10.0)
    curriculum_context: CurriculumContext
    material_scope: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    answers: list[AnswerOption] = Field(min_length=3)
    correct_answers: list[str] = Field(min_length=1)
    solution_steps: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    generation_seed: int = Field(ge=0)
    validation_checks: list[str] = Field(min_length=1)
    validation_status: Literal["validated"] = "validated"
    generator_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_answer_pool(self):
        if not self.skill_ids:
            self.skill_ids = [self.skill_id]
        if self.skill_id not in self.skill_ids:
            self.skill_ids.insert(0, self.skill_id)
        if len(self.skill_ids) != len(set(self.skill_ids)):
            raise ValueError("Task skill IDs must be unique")
        if len(self.subskill_ids) != len(set(self.subskill_ids)):
            raise ValueError("Task subskill IDs must be unique")
        if any(skill_id not in self.skill_ids for skill_id in self.subskills_by_skill):
            raise ValueError("Task subskills must belong to a listed skill")
        option_ids = [option.id for option in self.answers]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("Answer option IDs must be unique")
        if len(self.correct_answers) != len(set(self.correct_answers)):
            raise ValueError("Correct answer IDs must be unique")
        if not set(self.correct_answers).issubset(option_ids):
            raise ValueError("Correct answers must refer to answer option IDs")
        normalized_texts = [option.text.strip().casefold() for option in self.answers]
        if len(normalized_texts) != len(set(normalized_texts)):
            raise ValueError("Answer option texts must be unique")
        if len(set(option_ids) - set(self.correct_answers)) < 2:
            raise ValueError("At least two distractors are required")
        return self


class PresentedOption(BaseModel):
    id: str
    text: str


class PresentedTask(BaseModel):
    presentation_id: str
    plan_id: str | None = None
    task_id: str
    task_type: Literal["closed_single_choice"]
    skill_id: str
    skill_ids: list[str] = Field(default_factory=list)
    subskill_ids: list[str] = Field(default_factory=list)
    subskills_by_skill: dict[str, list[str]] = Field(default_factory=dict)
    difficulty: int
    statement: str
    options: list[PresentedOption] = Field(min_length=2)


class TaskResult(BaseModel):
    presentation_id: str
    selected_option_id: str
    correct: bool
    score: float = Field(ge=0.0, le=1.0)

