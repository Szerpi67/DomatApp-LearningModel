r"""Opt-in tests that call the live Gemini API and report request latency.

Run with:
    $env:RUN_GEMINI_INTEGRATION = "1"
    .venv\Scripts\python.exe -m pytest -s task_generator\tests\test_gemini_api_integration.py
"""

import json
import os
from pathlib import Path
from time import perf_counter

import pytest

from planner.models.learning_plan import PlanItem, TaskAllocation
from planner.models.planner_input import CurriculumContext
from profiler.navigator.skill_navigator import SkillNavigator
from task_generator.models import TaskGenerationRequest
from task_generator.service import TaskGeneratorService

RUN_GEMINI_INTEGRATION = "1"

pytestmark = pytest.mark.skipif(
    not RUN_GEMINI_INTEGRATION,
    reason="Set RUN_GEMINI_INTEGRATION=1 to call the live Gemini API.",
)

ROOT = Path(__file__).resolve().parents[2]


def load_navigator():
    skills = []
    for path in sorted((ROOT / "data_files" / "skills").rglob("*.json")):
        with path.open(encoding="utf-8") as stream:
            skills.extend(json.load(stream)["skills"])
    return SkillNavigator.from_skill_records(skills)


def test_live_gemini_streaming_first_task_and_batch_latency():
    navigator = load_navigator()
    item = PlanItem(
        skill_id="MAT-FRA-009",
        role="main",
        target_difficulty=10,
        material_scope="Grade 7 standard curriculum",
        reason="Live API latency check",
        task_allocations=[
            TaskAllocation(task_type="closed_single_choice", count=2)
        ],
    )
    request = TaskGenerationRequest(
        plan_id="gemini-live-integration",
        plan_item=item,
        curriculum_context=CurriculumContext(
            grade=7,
            education_stage="primary school",
            curriculum_level="STANDARD",
        ),
    )
    generator = TaskGeneratorService(navigator)
    started = perf_counter()
    task_stream = iter(generator.generate_batch_stream(request, seed=20261031))
    first_task = next(task_stream)
    first_task_seconds = perf_counter() - started
    tasks = [first_task, *task_stream]
    total_seconds = perf_counter() - started

    assert len(tasks) == 2
    assert all(task.validation_status == "validated" for task in tasks)
    assert first_task_seconds > 0
    assert total_seconds >= first_task_seconds
    print(
        "Gemini live streaming latency: "
        f"first_task={first_task_seconds:.2f}s, total_batch={total_seconds:.2f}s"
    )
    print(first_task)
