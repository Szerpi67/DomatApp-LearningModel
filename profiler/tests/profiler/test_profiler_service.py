
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from profiler.models.learning_event import LearningEvent
from profiler.repositories.profile_repository import InMemoryProfileRepository
from profiler.services.profiler_service import ProfilerService


@pytest.fixture
def repository():
    return InMemoryProfileRepository()


@pytest.fixture
def service(repository):
    return ProfilerService(repository)


def make_event(
    correct: bool = True,
    skill_id: str = "MAT-FRA-001",
    difficulty: int = 5,
    event_id: str | None = None,
    skipped: bool = False,
):
    return LearningEvent.model_validate({
        "event_id": event_id or str(uuid4()),
        "user_id": "student-001",
        "session_id": "session-001",
        "task_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc),
        "task": {
            "type": "OPEN",
            "difficulty": difficulty,
            "primary_skill_id": skill_id,
            "skills": [
                {"skill_id": skill_id, "weight": 1.0}
            ],
        },
        "result": {
            "correct": correct if not skipped else False,
            "score": float(correct) if not skipped else 0.0,
            "response_time_ms": 12000,
            "attempts": 1,
            "hints_used": 0,
            "skipped": skipped,
        },
        "context": {
            "purpose": "PRACTICE",
        },
    })


def test_correct_answer_increases_ability(service):
    profile = service.process_event(make_event(correct=True))
    skill = profile.skills["MAT-FRA-001"]
    print(profile, skill)
    assert skill.ability > 0.0
    assert 0.0 < skill.uncertainty < 1.0
    assert skill.observations == 1


def test_incorrect_answer_decreases_ability(service):
    profile = service.process_event(make_event(correct=False))
    skill = profile.skills["MAT-FRA-001"]

    assert skill.ability < 0.0
    assert 0.0 < skill.uncertainty < 1.0
    assert skill.observations == 1


def test_multiple_events_update_profile(service):
    for _ in range(5):
        profile = service.process_event(make_event(correct=True))

    skill = profile.skills["MAT-FRA-001"]

    assert skill.ability > 0.0
    assert skill.uncertainty < 1.0
    assert skill.observations == 5


def test_duplicate_event_is_not_processed_twice(service):
    event = make_event(correct=True, event_id="event-123")

    first = service.process_event(event)
    second = service.process_event(event)

    first_skill = first.skills["MAT-FRA-001"]
    second_skill = second.skills["MAT-FRA-001"]

    assert second_skill.observations == 1
    assert second_skill.ability == first_skill.ability
    assert second_skill.uncertainty == first_skill.uncertainty


def test_skipped_task_does_not_update_ability(service):
    profile = service.process_event(
        make_event(correct=False, skipped=True)
    )

    skill = profile.skills["MAT-FRA-001"]

    assert skill.ability == 0.0
    assert skill.uncertainty == 1.0
    assert skill.observations == 0


def test_different_skills_have_independent_states(service):
    service.process_event(
        make_event(correct=True, skill_id="MAT-FRA-001")
    )

    profile = service.process_event(
        make_event(correct=False, skill_id="MAT-FRA-011")
    )

    assert profile.skills["MAT-FRA-001"].ability > 0.0
    assert profile.skills["MAT-FRA-011"].ability < 0.0
    assert len(profile.skills) == 2
