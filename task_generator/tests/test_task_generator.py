import json
from datetime import datetime, timezone
from pathlib import Path
import random
from io import BytesIO
from urllib.error import HTTPError

import pytest

from planner.models.planner_input import CurriculumContext, PlannerInput
from planner.models.learning_plan import PlanItem
from planner.services.planner_service import PlannerService
from profiler.models.skill_state import SkillState
from profiler.models.student_profile import StudentProfile
from profiler.navigator.skill_navigator import SkillNavigator
from profiler.repositories.profile_repository import InMemoryProfileRepository
from profiler.services.profiler_service import ProfilerService
from task_generator.contracts import TASK_GENERATOR_V0_SCHEMA
from task_generator.models import TaskGenerationRequest
from task_generator.profiler_adapter import build_learning_event
from task_generator.service import (
    TaskGenerationError,
    TaskGeneratorService,
    TaskPresentationService,
)
import task_generator.service as task_generator_service


ROOT = Path(__file__).resolve().parents[2]
CONTEXT = CurriculumContext(
    grade=4,
    education_stage="primary school",
    curriculum_level="STANDARD",
)
TARGET_SKILL = "MAT-FRA-011"


@pytest.fixture(autouse=True)
def fake_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-key")
    calls = {"batch_stream": 0}

    def generate(self, request, seed):
        value = 20 + seed % 100
        return (
            f"Oblicz: {value} + 2.",
            [str(value + 2)],
            [str(value + 1), str(value + 3), str(value + 4), str(value + 5), str(value + 6)],
            [f"Dodaj 2 do {value}.", f"Wynik to {value + 2}."],
            "Add the terms to get the result.",
        )

    monkeypatch.setattr(TaskGeneratorService, "_generate_with_gemini", generate)

    def generate_batch_stream(self, request, seed=None):
        calls["batch_stream"] += 1
        task_count = sum(item.count for item in request.plan_item.task_allocations)
        batch_seed = seed or 0
        for index in range(task_count):
            value = 20 + (batch_seed + index) % 100
            result = {
                "statement": f"Oblicz: {value} + 2.",
                "correct_answer": str(value + 2),
                "distractors": [str(value + offset) for offset in (1, 3, 4, 5, 6)],
                "solution_steps": [f"Dodaj 2 do {value}.", f"Wynik to {value + 2}."],
                "explanation": "Add the terms to get the result.",
            }
            yield self._build_gemini_task(request, result, batch_seed + index)

    monkeypatch.setattr(TaskGeneratorService, "generate_batch_stream", generate_batch_stream)
    return calls

def load_navigator():
    print("Loading navigator")
    records = []
    folder = Path("data_files/skills")
    filenames = [file.name for file in folder.glob("*.json")]
    print(f"Found {len(filenames)} skills.")
    for filename in filenames:
        print("loading:",filename)
        with (ROOT / "data_files" / "skills" / filename).open(encoding="utf-8") as stream:
            records.extend(json.load(stream)["skills"])
    return SkillNavigator.from_skill_records(records)


def make_plan():
    navigator = load_navigator()
    user_id = "task-generator-student"
    profile = StudentProfile(
        user_id=user_id,
        skills={
            skill_id: SkillState(
                skill_id=skill_id,
                ability=5.0,
                uncertainty=0.2,
                observations=10,
                working_difficulty=3,
                last_practiced_at=datetime.now(timezone.utc),
            )
            for skill_id in ("MAT-FRA-001", "MAT-ARI-004")
        },
        updated_at=datetime.now(timezone.utc),
    )
    plan = PlannerService(navigator).create_plan(
        PlannerInput(
            user_id=user_id,
            target_skill_id=TARGET_SKILL,
            student_profile=profile,
            curriculum_context=CONTEXT,
        )
    )
    return navigator, user_id, profile, plan


def generate_template(seed=5):
    navigator, user_id, profile, plan = make_plan()
    assert plan.items[-1].skill_id == TARGET_SKILL
    generator = TaskGeneratorService(navigator, rng=random.Random(seed))
    template = generator.generate(
        TaskGenerationRequest(
            plan_id=plan.plan_id,
            plan_item=plan.items[-1],
            curriculum_context=CONTEXT,
        ),
        seed=seed,
    )
    return navigator, user_id, profile, plan, template


def test_fraction_task_matches_planner_and_v0_json_contract():
    _, _, _, plan, task = generate_template()

    payload = task.model_dump(mode="json")
    assert set(TASK_GENERATOR_V0_SCHEMA["required"]).issubset(payload)
    assert payload["task_type"] == "closed_single_choice"
    assert payload["plan_id"] == plan.plan_id
    assert payload["skill_id"] == plan.target_skill_id
    assert payload["difficulty"] == round(plan.items[-1].target_difficulty)
    assert len(payload["answers"]) >= 4
    assert len(payload["correct_answers"]) == 1
    assert set(payload["correct_answers"]).issubset(
        {answer["id"] for answer in payload["answers"]}
    )
    assert payload["validation_status"] == "validated"


def test_presentations_randomize_choices_and_keep_answer_key_private():
    _, _, _, _, task = generate_template()
    presenter = TaskPresentationService(rng=random.Random(12))

    presentations = [presenter.present(task) for _ in range(8)]

    option_sets = [tuple(sorted(option.text for option in item.options)) for item in presentations]
    assert len(set(option_sets)) > 1
    for item in presentations:
        assert len(item.options) == 4
        assert len({option.id for option in item.options}) == 4
        assert "correct_answers" not in item.model_dump()
        assert "correct_option_id" not in item.model_dump()


def test_generation_seed_reproduces_task_content():
    _, _, _, _, first = generate_template(seed=2026)
    _, _, _, _, second = generate_template(seed=2026)

    assert first.statement == second.statement
    assert [item.text for item in first.answers] == [
        item.text for item in second.answers
    ]
    assert first.correct_answers == second.correct_answers
    assert first.generation_seed == second.generation_seed == 2026


def test_plan_allocations_control_batch_task_count_and_type(fake_gemini):
    navigator, _, _, plan, _ = generate_template()
    plan_item = plan.items[-1]
    assert [(item.task_type, item.count) for item in plan_item.task_allocations] == [
        ("closed_single_choice", 2)
    ]
    generator = TaskGeneratorService(navigator)
    request = TaskGenerationRequest(
        plan_id=plan.plan_id,
        plan_item=plan_item,
        curriculum_context=CONTEXT,
    )

    tasks = generator.generate_batch(request, seed=100)

    assert len(tasks) == 2
    assert all(task.task_type == "closed_single_choice" for task in tasks)
    assert fake_gemini == {"batch_stream": 1}


def test_gemini_retries_temporary_service_unavailable(monkeypatch):
    calls = 0

    def flaky_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {"Retry-After": "0"},
                BytesIO(b"temporary overload"),
            )
        return BytesIO(b"ok")

    monkeypatch.setattr(task_generator_service, "urlopen", flaky_urlopen)
    monkeypatch.setattr(task_generator_service.time, "sleep", lambda _: None)
    request = task_generator_service.Request("https://example.test")

    with task_generator_service._urlopen_with_retries(request, timeout=1) as response:
        assert response.read() == b"ok"

    assert calls == 2


def test_stream_parser_yields_first_complete_task_before_batch_finishes():
    first_chunk = '[{"statement":"first task"}'
    first, cursor, closed = task_generator_service._extract_json_array_items(
        first_chunk, 0
    )

    assert first == [{"statement": "first task"}]
    assert closed is False

    second_chunk = first_chunk + ',{"statement":"second task"}]'
    second, _, closed = task_generator_service._extract_json_array_items(
        second_chunk, cursor
    )

    assert second == [{"statement": "second task"}]
    assert closed is True


def test_answer_is_graded_and_profiler_receives_one_skill_event():
    navigator, user_id, _, plan, task = generate_template(seed=11)
    presenter = TaskPresentationService(rng=random.Random(7))
    displayed_task = presenter.present(task)
    correct_option = next(
        option
        for option in displayed_task.options
        if presenter.grade(displayed_task.presentation_id, option.id).correct
    )
    graded = presenter.grade(displayed_task.presentation_id, correct_option.id)
    event = build_learning_event(
        user_id=user_id,
        session_id="session-001",
        task=task,
        presentation=displayed_task,
        result=graded,
        response_time_ms=9000,
    )

    repository = InMemoryProfileRepository()
    profile = ProfilerService(repository).process_event(event)

    assert graded.correct is True
    assert event.task.primary_skill_id == TARGET_SKILL
    assert len(event.task.skills) == 1
    assert event.task.difficulty == task.difficulty
    assert event.context.plan_id == plan.plan_id
    assert event.task_id == displayed_task.presentation_id
    assert event.task.task_template_id == task.task_id
    assert profile.skills[TARGET_SKILL].observations == 1
    assert profile.skills[TARGET_SKILL].ability > 0.0


def test_grader_rejects_an_option_that_was_not_presented():
    _, _, _, _, task = generate_template()
    presenter = TaskPresentationService(rng=random.Random(7))
    presentation = presenter.present(task)

    with pytest.raises(TaskGenerationError) as error:
        presenter.grade(presentation.presentation_id, "not-shown")

    assert error.value.code == "invalid_answer"


def test_invalid_or_unsupported_generation_fails_explicitly():
    navigator, _, _, plan, _ = generate_template()
    generator = TaskGeneratorService(navigator, rng=random.Random(1))
    unsupported_item = plan.items[-1].model_copy(
        update={"skill_id": "MAT-NOT-IN-SKILL-TREE"}
    )

    with pytest.raises(TaskGenerationError) as error:
        generator.generate(
            TaskGenerationRequest(
                plan_id=plan.plan_id,
                plan_item=unsupported_item,
                curriculum_context=CONTEXT,
            )
        )

    assert error.value.code == "unknown_skill"


def test_out_of_curriculum_task_is_rejected():
    navigator, _, _, plan, _ = generate_template()
    generator = TaskGeneratorService(navigator, rng=random.Random(1))

    with pytest.raises(TaskGenerationError) as error:
        generator.generate(
            TaskGenerationRequest(
                plan_id=plan.plan_id,
                plan_item=plan.items[-1],
                curriculum_context=CurriculumContext(
                    grade=3,
                    education_stage="primary school",
                    curriculum_level="STANDARD",
                ),
            )
        )

    assert error.value.code == "outside_curriculum_context"


@pytest.mark.parametrize(
    "skill_id",
    [
        *(f"MAT-FRA-{number:03d}" for number in range(1, 13)),
        "MAT-ARI-004",
        "MAT-ARI-003",
        "MAT-ARI-002",
        "MAT-ARI-001",
    ],
)
@pytest.mark.parametrize("difficulty", [1, 5, 10])
def test_supported_skill_generators_return_valid_tasks(skill_id, difficulty):
    navigator = load_navigator()
    generator = TaskGeneratorService(navigator)
    request = TaskGenerationRequest(
        plan_id="plan-generator-coverage",
        plan_item=PlanItem(
            skill_id=skill_id,
            role="main",
            target_difficulty=difficulty,
            material_scope="Grade 4 standard curriculum",
            reason="Generator coverage test",
        ),
        curriculum_context=CONTEXT,
    )

    task = generator.generate(request, seed=2026)

    assert task.skill_id == skill_id
    assert task.difficulty == difficulty
    assert task.validation_status == "validated"
    assert "gemini_self_check_prompt" in task.validation_checks
    assert len(task.correct_answers) >= 1
    assert len(task.answers) >= 3


def test_planner_generator_profiler_progress_through_fraction_domain():
    navigator = load_navigator()
    planner = PlannerService(navigator)
    generator = TaskGeneratorService(navigator)
    presenter = TaskPresentationService(rng=random.Random(8))
    repository = InMemoryProfileRepository()
    repository.save(
        StudentProfile(
            user_id="fraction-domain-progress",
            skills={
                "MAT-ARI-002": SkillState(
                    skill_id="MAT-ARI-002",
                    ability=5.0,
                    uncertainty=0.2,
                    observations=10,
                    working_difficulty=3,
                    last_practiced_at=datetime.now(timezone.utc),
                )
            },
            updated_at=datetime.now(timezone.utc),
        )
    )
    profiler = ProfilerService(repository)
    session_id = "fraction-domain-progress-session"
    practiced_skills = []

    for session_number in range(1, 25):
        profile = repository.get("fraction-domain-progress")
        plan = planner.create_plan(
            PlannerInput(
                user_id=profile.user_id,
                selected_domain="fractions",
                student_profile=profile,
                curriculum_context=CONTEXT,
            )
        )
        assert plan.status != "blocked"
        plan_item = plan.items[0]
        task = generator.generate(
            TaskGenerationRequest(
                plan_id=plan.plan_id,
                plan_item=plan_item,
                curriculum_context=CONTEXT,
            ),
            seed=session_number,
        )
        presentation = presenter.present(task)
        correct_option = next(
            option
            for option in presentation.options
            if presenter.grade(presentation.presentation_id, option.id).correct
        )
        result = presenter.grade(presentation.presentation_id, correct_option.id)
        event = build_learning_event(
            user_id=profile.user_id,
            session_id=session_id,
            task=task,
            presentation=presentation,
            result=result,
            response_time_ms=7000,
        )
        profiler.process_event(event)
        practiced_skills.append(plan_item.skill_id)

    assert practiced_skills[:3] == ["MAT-FRA-001"] * 3
    assert "MAT-FRA-002" in practiced_skills
    assert len(set(practiced_skills)) >= 3
