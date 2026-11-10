
from datetime import datetime

from pydantic import BaseModel, Field, AwareDatetime

from profiler.models.task_info import TaskInfo
from profiler.models.task_result import TaskResult
from profiler.models.learning_context import LearningContext


class LearningEvent(BaseModel):
    event_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)

    timestamp: AwareDatetime

    task: TaskInfo
    result: TaskResult
    context: LearningContext
