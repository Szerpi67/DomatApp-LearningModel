from datetime import datetime, timezone
from uuid import uuid4

from profiler.models.learning_context import LearningPurpose
from profiler.models.learning_event import LearningEvent
from profiler.models.task_info import TaskInfo, TaskSkill, TaskType
from profiler.models.task_result import TaskResult as ProfilerTaskResult

from task_generator.models import PresentedTask, TaskResult, TaskTemplate


def build_learning_event(
    *,
    user_id: str,
    session_id: str,
    task: TaskTemplate,
    presentation: PresentedTask,
    result: TaskResult,
    response_time_ms: int,
    attempts: int = 1,
    hints_used: int = 0,
) -> LearningEvent:
    """Adapt an evaluated presentation to the existing Profiler event contract."""
    if presentation.presentation_id != result.presentation_id:
        raise ValueError("Result does not belong to this task presentation")
    if presentation.task_id != task.task_id:
        raise ValueError("Presentation does not belong to this task template")
    if (
        presentation.skill_id != task.skill_id
        or (presentation.skill_ids or [presentation.skill_id]) != task.skill_ids
        or presentation.difficulty != task.difficulty
    ):
        raise ValueError("Presentation metadata does not match the generated task")

    return LearningEvent(
        event_id=str(uuid4()),
        user_id=user_id,
        session_id=session_id,
        task_id=presentation.presentation_id,
        timestamp=datetime.now(timezone.utc),
        task=TaskInfo(
            type=TaskType.SINGLE_CHOICE,
            difficulty=task.difficulty,
            primary_skill_id=task.skill_id,
            skills=[
                TaskSkill(
                    skill_id=skill_id,
                    weight=1.0 / len(task.skill_ids),
                    subskill_ids=task.subskills_by_skill.get(skill_id, []),
                )
                for skill_id in task.skill_ids
            ],
            task_template_id=task.task_id,
        ),
        result=ProfilerTaskResult(
            correct=result.correct,
            score=result.score,
            response_time_ms=response_time_ms,
            attempts=attempts,
            hints_used=hints_used,
        ),
        context={
            "purpose": LearningPurpose.PRACTICE,
            "plan_id": task.plan_id,
        },
    )
