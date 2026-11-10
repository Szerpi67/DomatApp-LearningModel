from typing import List, Literal
from pydantic import BaseModel, Field, model_validator

PlanStatus = Literal["ready", "needs_diagnostic", "blocked"]
ItemRole = Literal["main", "remediation", "diagnostic", "review", "integration"]

class TaskAllocation(BaseModel):
    """Number of tasks of a given type for one planned skill."""

    task_type: str = Field(min_length=1)
    count: int = Field(ge=1, le=20)


class PlanItem(BaseModel):
    skill_id: str
    skill_ids: List[str] = Field(default_factory=list, description="Wszystkie umiejętności ćwiczone w zadaniu; skill_id jest główną.")
    subskill_ids: List[str] = Field(default_factory=list, description="Cele szczegółowe/objectives w obrębie umiejętności.")
    subskills_by_skill: dict[str, List[str]] = Field(default_factory=dict)
    role: ItemRole
    target_difficulty: float = Field(..., ge=1.0, le=10.0, description="Docelowa trudność 1–10")
    material_scope: str = Field(..., description="Zakres materiału dla tego kroku")
    reason: str = Field(..., description="Powód wyboru elementu")
    task_allocations: List[TaskAllocation] = Field(
        default_factory=lambda: [TaskAllocation(task_type="closed_single_choice", count=1)]
    )

    @model_validator(mode="after")
    def validate_task_allocations(self):
        if not self.skill_ids:
            self.skill_ids = [self.skill_id]
        if self.skill_id not in self.skill_ids:
            self.skill_ids.insert(0, self.skill_id)
        if len(self.skill_ids) != len(set(self.skill_ids)):
            raise ValueError("Plan item skill IDs must be unique")
        if len(self.subskill_ids) != len(set(self.subskill_ids)):
            raise ValueError("Plan item subskill IDs must be unique")
        if not self.subskills_by_skill and self.subskill_ids:
            self.subskills_by_skill = {self.skill_id: list(self.subskill_ids)}
        for skill_id, subskills in self.subskills_by_skill.items():
            if skill_id not in self.skill_ids:
                raise ValueError("Subskills must belong to a skill on the plan item")
            if len(subskills) != len(set(subskills)):
                raise ValueError("Subskill IDs must be unique per skill")
        task_types = [allocation.task_type for allocation in self.task_allocations]
        if not task_types or len(task_types) != len(set(task_types)):
            raise ValueError("Each plan item needs unique task type allocations")
        return self

class LearningPlan(BaseModel):
    plan_id: str
    target_skill_id: str
    items: List[PlanItem] = Field(default_factory=list)
    status: PlanStatus = "ready"
    reason: str = Field(..., description="Uzasadnienie decyzji plannera")
