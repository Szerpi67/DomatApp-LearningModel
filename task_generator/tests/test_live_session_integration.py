r"""Opt-in live Gemini + MongoDB learner-session integration test.

Run in PowerShell with:
    $env:RUN_GEMINI_SESSION_SIMULATION = "1"
    .venv\Scripts\python.exe -m pytest -s -q task_generator\tests\test_live_session_integration.py

The test creates uniquely named integration records in MONGODB_DATABASE and
prints the full task/answer/profile progression to the test console.
"""

from datetime import datetime, timezone
import json
import os
import sys
from time import perf_counter
from uuid import uuid4

import pytest

from main import load_skill_navigator
from planner.models.learning_plan import TaskAllocation
from planner.models.planner_input import CurriculumContext, PlannerInput
from planner.services.planner_service import PlannerService
from profiler.models.student_profile import StudentProfile
from profiler.repositories.mongo_profile_repository import MongoProfileRepository
from profiler.services.profiler_service import ProfilerService
from task_generator.cache import CachedTaskService
from task_generator.models import TaskGenerationRequest
from task_generator.profiler_adapter import build_learning_event
from task_generator.repositories.mongo_task_store import MongoTaskStore
from task_generator.service import TaskGeneratorService, TaskPresentationService

RUN_GEMINI_INTEGRATION = "1"

pytestmark = pytest.mark.skipif(
    RUN_GEMINI_INTEGRATION != "1",
    reason="Set RUN_GEMINI_SESSION_SIMULATION=1 to call live Gemini and MongoDB.",
)


def test_live_gemini_session_persists_task_answer_event_and_profile():
    run_id = str(uuid4())
    user_id = f"gemini-session-test-{run_id}"
    plan_id = f"gemini-session-plan-{run_id}"
    started = perf_counter()
    store = MongoTaskStore.from_environment()
    profiles = MongoProfileRepository.from_environment()
    report = None
    try:
        store.ping()
        profiles.ping()
        navigator = load_skill_navigator()
        planner = PlannerService(navigator)
        generator = TaskGeneratorService(navigator)
        cached_tasks = CachedTaskService(generator, store)
        presenter = TaskPresentationService(key_store=store)
        profiler = ProfilerService(profiles)
        context = CurriculumContext(
            grade=8,
            education_stage="primary school",
            curriculum_level="STANDARD",
            curriculum_version=f"live-session-{run_id}",
        )
        initial_profile = StudentProfile(
            user_id=user_id,
            updated_at=datetime.now(timezone.utc),
        )
        profiles.save(initial_profile)
        plan = planner.create_plan(
            PlannerInput(
                user_id=user_id,
                target_skill_id="MAT-FRA-011",
                student_profile=initial_profile,
                curriculum_context=context,
            )
        )
        assert plan.status == "ready" and plan.items
        plan_item = next(item for item in plan.items if item.role == "main")
        plan_item = plan_item.model_copy(
            update={
                "material_scope": f"{plan_item.material_scope} [live test {run_id}]",
                "task_allocations": [
                    TaskAllocation(task_type="closed_single_choice", count=1)
                ],
            }
        )
        request = TaskGenerationRequest(
            plan_id=plan_id,
            plan_item=plan_item,
            curriculum_context=context,
        )

        task = next(cached_tasks.stream_tasks(request, student_id=user_id))
        presented = presenter.present(task)
        # Simulate a learner selecting the first displayed option without
        # consulting the hidden answer key.
        selected_option = presented.options[0]
        result = presenter.grade(presented.presentation_id, selected_option.id)
        correct_option = next(
            option
            for option in presented.options
            if presenter.grade(presented.presentation_id, option.id).correct
        )
        previous_profile = profiles.get(user_id)
        previous_state = previous_profile.skills.get(task.skill_id)
        event = build_learning_event(
            user_id=user_id,
            session_id=f"live-session-{run_id}",
            task=task,
            presentation=presented,
            result=result,
            response_time_ms=1250,
        )
        updated_profile = profiler.process_event(event)
        updated_state = updated_profile.skills[task.skill_id]

        # Re-read all persisted records instead of relying on in-memory objects.
        saved_task = store.get_task(task.task_id)
        saved_presentation = store.get(presented.presentation_id)
        saved_event = profiles.get_event(event.event_id)
        saved_profile = profiles.get(user_id)
        assert saved_task is not None
        assert saved_presentation is not None
        assert saved_event is not None and saved_event.event_id == event.event_id
        assert saved_profile is not None
        assert saved_profile.skills[task.skill_id].observations == 1
        assert result.score in (0.0, 1.0)

        report = {
            "run_id": run_id,
            "database": store.database.name,
            "user_id": user_id,
            "task_id": task.task_id,
            "presentation_id": presented.presentation_id,
            "event_id": event.event_id,
            "plan_id": plan_id,
            "skill_id": task.skill_id,
            "difficulty": task.difficulty,
            "question": presented.statement,
            "options": {
                "ABCD"[index]: option.text
                for index, option in enumerate(presented.options)
            },
            "simulated_answer": selected_option.text,
            "correct_answer": correct_option.text,
            "correct": result.correct,
            "profile_update": {
                "ability_before": previous_state.ability if previous_state else 0.0,
                "ability_after": updated_state.ability,
                "uncertainty_after": updated_state.uncertainty,
                "observations": updated_state.observations,
                "working_difficulty": updated_state.working_difficulty,
            },
            "persisted": {
                "task": saved_task is not None,
                "presentation_answer_key": saved_presentation is not None,
                "learning_event": saved_event is not None,
                "student_profile": saved_profile is not None,
            },
            "elapsed_seconds": round(perf_counter() - started, 2),
        }
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print("\nLIVE GEMINI SESSION REPORT")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        profiles.close()
        store.close()

