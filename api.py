from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import fastapi
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import Field
from pydantic import BaseModel
from pydantic import model_validator

from main import load_skill_navigator
from planner.models.learning_plan import LearningPlan
from planner.models.planner_input import CurriculumContext, PlannerInput
from planner.services.planner_service import PlannerService
from profiler.repositories.mongo_profile_repository import MongoProfileRepository
from profiler.models.student_profile import StudentProfile
from profiler.services.profiler_service import ProfilerService
from task_generator.cache import CachedTaskService
from task_generator.models import PresentedTask, TaskGenerationRequest
from task_generator.profiler_adapter import build_learning_event
from task_generator.repositories.mongo_task_store import MongoTaskStore
from task_generator.service import TaskGenerationError, TaskGeneratorService, TaskPresentationService


SKILLS_DIRECTORY = Path(__file__).resolve().parent / "data_files" / "skills"


class TaskPoolPrefillRequest(TaskGenerationRequest):
    pool_size: int = Field(ge=1, le=100)


class TaskDeliveryRequest(TaskGenerationRequest):
    student_id: str = Field(min_length=1, max_length=200)


class LearningPlanRequest(BaseModel):
    student_id: str = Field(min_length=1, max_length=200)
    target_skill_id: str | None = None
    selected_domain: str | None = None
    curriculum_context: CurriculumContext

    @model_validator(mode="after")
    def validate_focus(self):
        if bool(self.target_skill_id) == bool(self.selected_domain):
            raise ValueError("Provide exactly one of target_skill_id or selected_domain")
        return self


class LearningAnswerRequest(BaseModel):
    student_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    selected_option_id: str = Field(min_length=1)
    response_time_ms: int = Field(ge=0)
    attempts: int = Field(default=1, ge=1)
    hints_used: int = Field(default=0, ge=0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = MongoTaskStore.from_environment()
    profiles = MongoProfileRepository.from_environment()
    try:
        store.ping()
        profiles.ping()
        generator = TaskGeneratorService(load_skill_navigator())
        navigator = generator.skill_navigator
        app.state.planner = PlannerService(navigator)
        app.state.task_store = store
        app.state.profile_repository = profiles
        app.state.profiler = ProfilerService(profiles)
        app.state.cached_tasks = CachedTaskService(generator, store)
        app.state.presenter = TaskPresentationService(key_store=store)
        yield
    finally:
        store.close()
        profiles.close()


app = FastAPI(
    title="EduBee Task API",
    version="1.0.0",
    description="Streams planned tasks, reuses MongoDB cache entries, and stores generated tasks.",
    lifespan=lifespan,
)


@app.get("/api/v1/categories")
def get_categories() -> dict[str, dict[str, object]]:
    """Return each skill category keyed by its JSON filename."""
    categories = {}
    for path in sorted(SKILLS_DIRECTORY.glob("*.json")):
        category = json.loads(path.read_text(encoding="utf-8"))
        categories[path.name] = {
            "id": path.name,
            "category_name": category["category_name"],
            "topics": list(
                dict.fromkeys(skill["topic"] for skill in category["skills"])
            ),
        }
    return categories



@app.post("/api/v1/learning-plans", response_model=LearningPlan)
def create_learning_plan(request: LearningPlanRequest):
    """Create and persist a personalized learning plan."""
    profile = app.state.profile_repository.get(request.student_id)
    if profile is None:
        profile = StudentProfile(
            user_id=request.student_id,
            updated_at=datetime.now(timezone.utc),
        )
        app.state.profile_repository.save(profile)
    planner_input = PlannerInput(
        user_id=request.student_id,
        target_skill_id=request.target_skill_id,
        selected_domain=request.selected_domain,
        student_profile=profile,
        curriculum_context=request.curriculum_context,
    )
    plan = app.state.planner.create_plan(planner_input)
    if plan.status != "blocked":
        profile.last_grade = request.curriculum_context.grade
        app.state.profile_repository.save(profile)
    app.state.task_store.save_learning_plan(
        plan, request.student_id, request.curriculum_context
    )
    return plan


@app.get("/api/v1/learning-plans/{plan_id}/tasks")
def get_learning_plan_tasks(plan_id: str, student_id: str):
    """Stream tasks assigned by a saved plan, filling cache misses on demand."""
    saved = app.state.task_store.get_learning_plan(plan_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Learning plan not found.")
    if saved["user_id"] != student_id:
        raise HTTPException(status_code=404, detail="Learning plan not found.")
    plan = LearningPlan.model_validate(saved["plan"])
    context = CurriculumContext.model_validate(saved["curriculum_context"])

    def events():
        delivered = 0
        try:
            for item_index, item in enumerate(plan.items):
                assigned = app.state.task_store.get_plan_tasks(plan_id, item_index)
                target_count = sum(allocation.count for allocation in item.task_allocations)
                for task in assigned[:target_count]:
                    task = task.model_copy(update={"plan_id": plan_id})
                    presented = app.state.presenter.present(task)
                    delivered += 1
                    yield json.dumps(
                        {
                            "event": "task",
                            "item_index": item_index,
                            "role": item.role,
                            "skill_ids": item.skill_ids,
                            "data": presented.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ) + "\n"

                remaining = max(0, target_count - len(assigned))
                if remaining == 0:
                    continue
                allocations = []
                still_needed = remaining
                for allocation in item.task_allocations:
                    already_assigned = sum(
                        1 for task in assigned if task.task_type == allocation.task_type
                    )
                    count = max(0, allocation.count - already_assigned)
                    if count:
                        allocations.append(allocation.model_copy(update={"count": count}))
                        still_needed -= count
                if still_needed > 0:
                    allocation = item.task_allocations[0]
                    allocations.append(allocation.model_copy(update={"count": still_needed}))
                request = TaskGenerationRequest(
                    plan_id=plan_id,
                    plan_item=item.model_copy(update={"task_allocations": allocations}),
                    curriculum_context=context,
                )
                for task in app.state.cached_tasks.stream_tasks(request, student_id=student_id):
                    app.state.task_store.add_plan_task(plan_id, item_index, task.task_id)
                    presented = app.state.presenter.present(task)
                    delivered += 1
                    yield json.dumps(
                        {
                            "event": "task",
                            "item_index": item_index,
                            "role": item.role,
                            "skill_ids": item.skill_ids,
                            "data": presented.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ) + "\n"
            yield json.dumps({"event": "done", "count": delivered}) + "\n"
        except TaskGenerationError as error:
            yield json.dumps(
                {"event": "error", "code": error.code, "message": str(error)},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
def health():
    app.state.task_store.ping()
    return {"status": "ok", "database": "mongodb"}


@app.post("/api/v1/tasks")
def generate_tasks(request: TaskDeliveryRequest):
    """Stream safe, presented tasks as newline-delimited JSON events."""

    def events():
        delivered = 0
        try:
            for task in app.state.cached_tasks.stream_tasks(
                request, student_id=request.student_id
            ):
                presented = app.state.presenter.present(task)
                delivered += 1
                yield json.dumps(
                    {"event": "task", "data": presented.model_dump(mode="json")},
                    ensure_ascii=False,
                ) + "\n"
            yield json.dumps({"event": "done", "count": delivered}) + "\n"
        except TaskGenerationError as error:
            yield json.dumps(
                {"event": "error", "code": error.code, "message": str(error)},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/tasks/{presentation_id}/answer")
def submit_task_answer(presentation_id: str, request: LearningAnswerRequest):
    """Grade a task answer, save its learning event, and update the learner profile."""
    presentation_data = app.state.task_store.get(presentation_id)
    if presentation_data is None:
        raise HTTPException(status_code=404, detail="Presentation not found or expired.")
    if not presentation_data.get("task_id") or not presentation_data.get("presented_task"):
        raise HTTPException(status_code=410, detail="Presentation cannot be used for grading.")

    task = app.state.task_store.get_task(presentation_data["task_id"])
    if task is None:
        raise HTTPException(status_code=404, detail="Task template not found.")
    presented = PresentedTask.model_validate(presentation_data["presented_task"])
    task = task.model_copy(update={"plan_id": presented.plan_id or task.plan_id})
    try:
        result = app.state.presenter.grade(
            presentation_id, request.selected_option_id
        )
    except TaskGenerationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    event = build_learning_event(
        user_id=request.student_id,
        session_id=request.session_id,
        task=task,
        presentation=presented,
        result=result,
        response_time_ms=request.response_time_ms,
        attempts=request.attempts,
        hints_used=request.hints_used,
    )
    profile = app.state.profiler.process_event(event)
    skills = {
        skill_id: profile.skills[skill_id].model_dump(mode="json")
        for skill_id in task.skill_ids
        if skill_id in profile.skills
    }
    return {
        "correct": result.correct,
        "score": result.score,
        "event_id": event.event_id,
        "solution_steps": task.solution_steps,
        "explanation": task.explanation,
        "skill_state": skills.get(task.skill_id),
        "skill_states": skills,
    }




@app.post("/api/v1/tasks/prefill")
def prefill_task_pool(
    request: TaskPoolPrefillRequest,
    x_admin_token: str | None = Header(default=None),
):
    """Fill a reusable MongoDB task pool; this cost-incurring route is admin-protected."""
    expected_token = os.getenv("TASK_ADMIN_TOKEN", "")
    if len(expected_token) < 32:
        raise HTTPException(
            status_code=503,
            detail="Configure TASK_ADMIN_TOKEN with at least 32 characters to enable pool prefill.",
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected_token):
        raise HTTPException(status_code=401, detail="Valid X-Admin-Token header required.")

    def events():
        try:
            for progress in app.state.cached_tasks.prefill_pool(request, request.pool_size):
                yield json.dumps(
                    {"event": "pool_progress", **progress}, ensure_ascii=False
                ) + "\n"
            yield json.dumps({"event": "done"}) + "\n"
        except TaskGenerationError as error:
            yield json.dumps(
                {"event": "error", "code": error.code, "message": str(error)},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
