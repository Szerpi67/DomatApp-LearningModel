
from pydantic import BaseModel, Field, model_validator


class TaskResult(BaseModel):
    correct: bool
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    response_time_ms: int = Field(ge=0)
    attempts: int = Field(ge=0)
    hints_used: int = Field(ge=0)
    skipped: bool = False

    @model_validator(mode="after")
    def validate_result(self):
        if self.skipped and self.correct:
            raise ValueError("Skipped task cannot be correct")

        if self.skipped and self.score != 0:
            raise ValueError("Skipped task must have score 0")

        return self
