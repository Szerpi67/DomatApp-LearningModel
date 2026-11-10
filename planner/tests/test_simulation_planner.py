"""Integration tests for planner decisions and profiler updates over sessions."""

from datetime import datetime, timezone
import json
from math import exp
from pathlib import Path
import random

import pytest

from planner.models.planner_input import CurriculumContext, PlannerInput
from planner.models.learning_plan import PlanItem
from planner.core.difficulty_selector import DifficultySelector
from planner.services.planner_service import PlannerService
from profiler.core.irt_model import difficulty_to_b
from profiler.models.learning_context import LearningPurpose
from profiler.models.learning_event import LearningEvent
from profiler.models.skill_state import SkillState
from profiler.models.student_profile import StudentProfile
from profiler.repositories.profile_repository import InMemoryProfileRepository
from profiler.services.profiler_service import ProfilerService


class JsonSkillNavigator:
    def __init__(self, skills):
        self.skills = {skill["id"]: skill for skill in skills}

    def has_skill(self, skill_id):
        return skill_id in self.skills

    def get_prerequisites(self, skill_id):
        return self.skills[skill_id].get("prerequisites", [])

    def get_skills(self):
        return list(self.skills.values())


SKILLS = [
    {"id": "fractions-basics", "prerequisites": []},
    {"id": "fraction-addition", "prerequisites": ["fractions-basics"]},
]
TARGET_SKILL = "fraction-addition"
CONTEXT = CurriculumContext(grade=4, education_stage="primary school")
SKILL_DATA_DIR = Path(__file__).resolve().parents[2] / "data_files" / "skills"


def load_production_skills():
    skills = []
    for filename in ("arithmetic.json", "fractions.json"):
        with (SKILL_DATA_DIR / filename).open(encoding="utf-8") as skill_file:
            skills.extend(json.load(skill_file)["skills"])
    return skills


def create_event(student_id, session_number, item, ability, rng, correct_override=None):
    """Generate a reproducible Rasch response and the event the profiler consumes."""
    difficulty = round(item.target_difficulty)
    probability = 1 / (1 + exp(difficulty_to_b(difficulty) - ability))
    correct = (
        correct_override
        if correct_override is not None
        else rng.random() < probability
    )
    timestamp = datetime.now(timezone.utc)
    return LearningEvent(
        event_id=f"{student_id}-event-{session_number}",
        user_id=student_id,
        session_id=f"{student_id}-session-{session_number}",
        task_id=f"{student_id}-task-{session_number}",
        timestamp=timestamp,
        task={
            "type": "OPEN",
            "difficulty": difficulty,
            "primary_skill_id": item.skill_id,
            "skills": [{"skill_id": item.skill_id, "weight": 1.0}],
        },
        result={
            "correct": correct,
            "score": float(correct),
            "response_time_ms": 8000,
            "attempts": 1,
            "hints_used": 0,
        },
        context={"purpose": LearningPurpose.PRACTICE},
    )


@pytest.mark.parametrize(
    ("student_id", "true_ability"),
    [("novice", -1.0), ("developing", 0.0), ("advanced", 1.0)],
)
def test_virtual_student_sessions_update_profiler_and_inform_planner(
    student_id, true_ability
):
    """Each session plans a task, simulates a response, then records it via ProfilerService."""
    rng = random.Random(2026)
    navigator = JsonSkillNavigator(SKILLS)
    planner = PlannerService(navigator)
    repository = InMemoryProfileRepository()
    profiler = ProfilerService(repository)

    for session_number in range(1, 31):
        profile = repository.get(student_id)
        if profile is None:
            profile = StudentProfile(
                user_id=student_id, updated_at=datetime.now(timezone.utc)
            )

        plan = planner.create_plan(
            PlannerInput(
                user_id=student_id,
                target_skill_id=TARGET_SKILL,
                student_profile=profile,
                curriculum_context=CONTEXT,
            )
        )

        assert plan.status == "ready"
        assert plan.items
        item = plan.items[0]
        assert item.skill_id in {"fractions-basics", TARGET_SKILL}

        profiler.process_event(
            create_event(student_id, session_number, item, true_ability, rng)
        )

    final_profile = repository.get(student_id)
    assert final_profile is not None
    assert sum(skill.observations for skill in final_profile.skills.values()) == 30
    assert final_profile.skills
    assert all(skill.uncertainty < 1.0 for skill in final_profile.skills.values())
    assert all(
        skill.last_practiced_at.tzinfo is not None
        for skill in final_profile.skills.values()
    )


def test_virtual_student_sessions_with_production_skill_files():
    """Run sessions against every skill from both production curriculum files."""
    skills = load_production_skills()
    navigator = JsonSkillNavigator(skills)
    skill_ids = set(navigator.skills)

    assert len(skills) == len(skill_ids)
    assert len(skill_ids) > len(SKILLS)
    assert all(
        prerequisite in skill_ids
        for skill in skills
        for prerequisite in skill.get("prerequisites", [])
    )

    planner = PlannerService(navigator)
    repository = InMemoryProfileRepository()
    profiler = ProfilerService(repository)
    student_id = "production-curriculum-student"
    rng = random.Random(2026)
    true_ability = 0.25
    sessions_per_target = 12
    session_number = 0

    # Exercise each production skill as a goal, retaining the same evolving
    # profile so later plans can use evidence gathered in earlier sessions.
    for target_skill_id in sorted(skill_ids):
        for _ in range(sessions_per_target):
            session_number += 1
            profile = repository.get(student_id)
            if profile is None:
                profile = StudentProfile(
                    user_id=student_id, updated_at=datetime.now(timezone.utc)
                )

            plan = planner.create_plan(
                PlannerInput(
                    user_id=student_id,
                    target_skill_id=target_skill_id,
                    student_profile=profile,
                    curriculum_context=CONTEXT,
                )
            )

            assert plan.status == "ready"
            assert plan.items
            assert all(item.skill_id in skill_ids for item in plan.items)

            profiler.process_event(
                create_event(
                    student_id,
                    session_number,
                    plan.items[0],
                    true_ability,
                    rng,
                )
            )

    final_profile = repository.get(student_id)
    assert final_profile is not None
    assert (
        sum(skill.observations for skill in final_profile.skills.values())
        == session_number
    )
    assert len(final_profile.skills) > 1


def test_difficulty_adapts_differently_for_weak_and_strong_students():
    outcomes = {}

    for student_id, true_ability in (("weak", -2.0), ("strong", 2.0)):
        planner = PlannerService(JsonSkillNavigator(SKILLS))
        repository = InMemoryProfileRepository()
        profiler = ProfilerService(repository)
        rng = random.Random(73)
        difficulties = []

        for session_number in range(1, 61):
            profile = repository.get(student_id)
            if profile is None:
                profile = StudentProfile(
                    user_id=student_id,
                    updated_at=datetime.now(timezone.utc),
                )
            plan = planner.create_plan(
                PlannerInput(
                    user_id=student_id,
                    target_skill_id=TARGET_SKILL,
                    student_profile=profile,
                    curriculum_context=CONTEXT,
                )
            )
            target_item = next(item for item in plan.items if item.role == "main")
            difficulties.append(target_item.target_difficulty)
            profiler.process_event(
                create_event(
                    student_id,
                    session_number,
                    target_item,
                    true_ability,
                    rng,
                )
            )

        final_profile = repository.get(student_id)
        outcomes[student_id] = (
            final_profile.skills[TARGET_SKILL].ability,
            difficulties[-1],
        )

    assert outcomes["strong"][0] > outcomes["weak"][0]
    assert outcomes["strong"][1] > outcomes["weak"][1]
    assert outcomes["strong"][1] > 4


def test_random_guessing_is_bounded_across_fixed_random_seeds():
    final_abilities = []

    for seed in (7, 19, 31, 47, 83):
        student_id = f"guessing-student-{seed}"
        planner = PlannerService(JsonSkillNavigator(SKILLS))
        repository = InMemoryProfileRepository()
        profiler = ProfilerService(repository)
        rng = random.Random(seed)

        for session_number in range(1, 41):
            profile = repository.get(student_id)
            if profile is None:
                profile = StudentProfile(
                    user_id=student_id,
                    updated_at=datetime.now(timezone.utc),
                )
            plan = planner.create_plan(
                PlannerInput(
                    user_id=student_id,
                    target_skill_id=TARGET_SKILL,
                    student_profile=profile,
                    curriculum_context=CONTEXT,
                )
            )
            item = next(item for item in plan.items if item.role == "main")
            guessed_correctly = rng.random() < 0.5
            profiler.process_event(
                create_event(
                    student_id,
                    session_number,
                    item,
                    ability=0.0,
                    rng=rng,
                    correct_override=guessed_correctly,
                )
            )

        state = repository.get(student_id).skills[TARGET_SKILL]
        final_abilities.append(state.ability)
        assert state.observations == 40
        assert 0.0 < state.uncertainty < 1.0

    # Across these fixed seeds, guessing should not produce wildly different
    # ability estimates. This checks stability, not calibration accuracy.
    assert max(final_abilities) - min(final_abilities) < 1.5


def test_production_targets_show_ability_progress_after_practice():
    skills = load_production_skills()
    navigator = JsonSkillNavigator(skills)

    for target_skill in skills:
        target_skill_id = target_skill["id"]
        student_id = f"progress-{target_skill_id}"
        initial_ability = 0.0
        initial_uncertainty = 1.0
        skill_states = {
            prerequisite: SkillState(
                skill_id=prerequisite,
                ability=5.0,
                uncertainty=0.2,
                observations=10,
                working_difficulty=3,
                last_practiced_at=datetime.now(timezone.utc),
            )
            for prerequisite in target_skill.get("prerequisites", [])
        }
        skill_states[target_skill_id] = SkillState(
            skill_id=target_skill_id,
            ability=initial_ability,
            uncertainty=initial_uncertainty,
            observations=0,
            working_difficulty=3,
            last_practiced_at=datetime.now(timezone.utc),
        )
        profile = StudentProfile(
            user_id=student_id,
            skills=skill_states,
            updated_at=datetime.now(timezone.utc),
        )
        repository = InMemoryProfileRepository()
        repository.save(profile)
        profiler = ProfilerService(repository)
        planner = PlannerService(navigator)
        rng = random.Random(target_skill_id)

        for session_number in range(1, 21):
            current_profile = repository.get(student_id)
            plan = planner.create_plan(
                PlannerInput(
                    user_id=student_id,
                    target_skill_id=target_skill_id,
                    student_profile=current_profile,
                    curriculum_context=CONTEXT,
                )
            )
            target_item = next(
                item for item in plan.items if item.role == "main"
            )
            profiler.process_event(
                create_event(
                    student_id,
                    session_number,
                    target_item,
                    ability=2.0,
                    rng=rng,
                )
            )

        final_state = repository.get(student_id).skills[target_skill_id]
        assert final_state.observations == 20
        assert final_state.ability > initial_ability
        assert final_state.uncertainty < initial_uncertainty


def test_planner_selects_next_skill_from_student_selected_domain():
    skills = load_production_skills()
    planner = PlannerService(JsonSkillNavigator(skills))
    profile = StudentProfile(
        user_id="student-selected-fractions",
        updated_at=datetime.now(timezone.utc),
        skills={
            "MAT-FRA-001": SkillState(
                skill_id="MAT-FRA-001",
                ability=3.0,
                uncertainty=0.2,
                observations=8,
                working_difficulty=3,
                last_practiced_at=datetime.now(timezone.utc),
            )
        },
    )

    plan = planner.create_plan(
        PlannerInput(
            user_id=profile.user_id,
            selected_domain="ulamki",
            student_profile=profile,
            curriculum_context=CONTEXT,
        )
    )

    assert plan.status == "ready"
    assert plan.target_skill_id == "MAT-FRA-002"
    assert plan.items[-1].skill_id == "MAT-FRA-002"
    assert any(
        item.skill_id == "MAT-FRA-002" and item.role == "main"
        for item in plan.items
    )
    assert plan.items[-1].role == "integration"


def test_domain_planner_waits_for_three_observations_before_advancing():
    skills = load_production_skills()
    planner = PlannerService(JsonSkillNavigator(skills))
    profile = StudentProfile(
        user_id="student-with-two-fraction-observations",
        updated_at=datetime.now(timezone.utc),
        skills={
            "MAT-FRA-001": SkillState(
                skill_id="MAT-FRA-001",
                ability=4.0,
                uncertainty=0.2,
                observations=2,
                working_difficulty=3,
                last_practiced_at=datetime.now(timezone.utc),
            )
        },
    )

    plan = planner.create_plan(
        PlannerInput(
            user_id=profile.user_id,
            selected_domain="fractions",
            student_profile=profile,
            curriculum_context=CONTEXT,
        )
    )

    assert plan.target_skill_id == "MAT-FRA-001"


def test_planner_starts_at_first_skill_in_domain_when_profile_has_no_data():
    planner = PlannerService(JsonSkillNavigator(load_production_skills()))
    profile = StudentProfile(
        user_id="new-student-learning-fractions",
        updated_at=datetime.now(timezone.utc),
    )

    plan = planner.create_plan(
        PlannerInput(
            user_id=profile.user_id,
            selected_domain="fractions",
            student_profile=profile,
            curriculum_context=CONTEXT,
        )
    )

    assert plan.target_skill_id == "MAT-FRA-001"
    assert plan.items[-1].skill_id == "MAT-FRA-001"
    assert any(
        item.skill_id == "MAT-FRA-001" and item.role == "main"
        for item in plan.items
    )
    assert plan.items[-1].role == "integration"
    assert plan.items[0].skill_id == "MAT-ARI-002"
    assert plan.items[0].role == "diagnostic"


def test_planner_does_not_select_skill_outside_curriculum_context():
    planner = PlannerService(JsonSkillNavigator(load_production_skills()))
    profile = StudentProfile(
        user_id="student-before-grade-four",
        updated_at=datetime.now(timezone.utc),
    )

    plan = planner.create_plan(
        PlannerInput(
            user_id=profile.user_id,
            selected_domain="fractions",
            student_profile=profile,
            curriculum_context=CurriculumContext(
                grade=3,
                education_stage="primary school",
                curriculum_level="STANDARD",
            ),
        )
    )

    assert plan.status == "blocked"
    assert plan.items == []
    assert "wybranego kontekstu" in plan.reason


def test_strong_low_uncertainty_student_is_offered_harder_work():
    navigator = JsonSkillNavigator(SKILLS)
    planner = PlannerService(navigator)
    repository = InMemoryProfileRepository()
    profiler = ProfilerService(repository)
    student_id = "strong-student"
    profile = StudentProfile(
        user_id=student_id,
        updated_at=datetime.now(timezone.utc),
        skills={
            TARGET_SKILL: SkillState(
                skill_id=TARGET_SKILL,
                ability=0.0,
                uncertainty=1.0,
                working_difficulty=3,
                last_practiced_at=datetime.now(timezone.utc),
            )
        },
    )
    starting_uncertainty = profile.skills[TARGET_SKILL].uncertainty
    repository.save(profile)
    rng = random.Random(31)
    target_item = None
    print("\n[Scenariusz: dobre wyniki i niska niepewność]")
    print("Start: umiejętność docelowa, ability=0.00, uncertainty=1.00, trudność=3")

    for session_number in range(1, 9):
        profile = repository.get(student_id)
        plan = planner.create_plan(
            PlannerInput(
                user_id=student_id,
                target_skill_id=TARGET_SKILL,
                student_profile=profile,
                curriculum_context=CONTEXT,
            )
        )
        target_item = next(item for item in plan.items if item.role == "main")
        print(
            f"Sesja {session_number}: Planner wybrał {target_item.skill_id} "
            f"(rola={target_item.role}, trudność={target_item.target_difficulty:g})"
        )
        profiler.process_event(
            create_event(
                student_id, session_number, target_item, 2.0, rng,
                correct_override=True,
            )
        )

    final_state = repository.get(student_id).skills[TARGET_SKILL]
    print(
        f"Po serii poprawnych odpowiedzi: ability={final_state.ability:.2f}, "
        f"uncertainty={final_state.uncertainty:.2f}; "
        f"zaproponowana trudność={target_item.target_difficulty:g}"
    )
    assert final_state.ability > 0.0
    assert final_state.uncertainty < starting_uncertainty
    assert target_item is not None
    assert target_item.target_difficulty > 3


def test_weak_prerequisite_is_planned_before_requested_goal():
    planner = PlannerService(JsonSkillNavigator(SKILLS))
    profile = StudentProfile(
        user_id="student-with-gap",
        updated_at=datetime.now(timezone.utc),
        skills={
            "fractions-basics": SkillState(
                skill_id="fractions-basics",
                ability=-2.0,
                uncertainty=0.2,
                working_difficulty=4,
                last_practiced_at=datetime.now(timezone.utc),
            )
        },
    )

    plan = planner.create_plan(
        PlannerInput(
            user_id=profile.user_id,
            target_skill_id=TARGET_SKILL,
            student_profile=profile,
            curriculum_context=CONTEXT,
        )
    )

    print("\n[Scenariusz: brak w umiejętności wymaganej]")
    print(f"Cel ucznia: {TARGET_SKILL}")
    for position, item in enumerate(plan.items, start=1):
        print(
            f"Krok {position}: {item.skill_id} (rola={item.role}, "
            f"trudność={item.target_difficulty:g}); powód: {item.reason}"
        )

    assert [item.skill_id for item in plan.items] == [
        "fractions-basics",
        TARGET_SKILL,
    ]
    remediation, goal = plan.items
    assert remediation.role == "remediation"
    assert remediation.target_difficulty == 4
    assert "brak" in remediation.reason.lower()
    assert goal.role == "main"
    assert goal.skill_id == TARGET_SKILL


def test_planner_tolerates_one_miss_but_reacts_to_a_run_of_misses():
    navigator = JsonSkillNavigator(SKILLS)
    planner = PlannerService(navigator)
    repository = InMemoryProfileRepository()
    profiler = ProfilerService(repository)
    student_id = "student-with-changing-results"
    repository.save(
        StudentProfile(
            user_id=student_id,
            updated_at=datetime.now(timezone.utc),
            skills={
                "fractions-basics": SkillState(
                    skill_id="fractions-basics",
                    ability=3.0,
                    uncertainty=1.0,
                    working_difficulty=5,
                    last_practiced_at=datetime.now(timezone.utc),
                )
            },
        )
    )
    rng = random.Random(47)
    print("\n[Scenariusz: seria błędów po dobrych wynikach]")
    print("Start: fractions-basics, ability=3.00, uncertainty=1.00, trudność=5")

    def current_plan():
        return planner.create_plan(
            PlannerInput(
                user_id=student_id,
                target_skill_id=TARGET_SKILL,
                student_profile=repository.get(student_id),
                curriculum_context=CONTEXT,
            )
        )

    before_misses = current_plan()
    assert before_misses.items[0].role == "main"
    print("Przed błędami: Planner kieruje ucznia do celu głównego.")
    # Keep task exposure fixed even while the planner still considers this
    # prerequisite mastered and therefore does not include it in the plan.
    observed_skill_item = PlanItem(
        skill_id="fractions-basics",
        role="remediation",
        target_difficulty=5,
        material_scope="",
        reason="Controlled response sequence for the simulation",
    )

    for session_number in range(1, 33):
        profiler.process_event(
            create_event(
                student_id,
                session_number,
                observed_skill_item,
                ability=3.0,
                rng=rng,
                correct_override=False,
            )
        )
        if session_number == 1:
            # A single surprising answer should not erase strong prior evidence.
            after_one_miss = current_plan()
            assert after_one_miss.items[0].role == "main"
            assert (
                after_one_miss.items[0].target_difficulty
                == before_misses.items[0].target_difficulty
            )
            state = repository.get(student_id).skills["fractions-basics"]
            print(
                f"Po pojedynczym błędzie: ability={state.ability:.2f}, "
                f"uncertainty={state.uncertainty:.2f}; Planner nadal kieruje do celu."
            )

    after_streak = current_plan()
    print("Po serii błędów:")
    for position, item in enumerate(after_streak.items, start=1):
        print(
            f"Krok {position}: {item.skill_id} (rola={item.role}, "
            f"trudność={item.target_difficulty:g}); powód: {item.reason}"
        )
    assert after_streak.items[0].skill_id == "fractions-basics"
    assert after_streak.items[0].role == "remediation"
    assert "brak" in after_streak.items[0].reason.lower()
    assert after_streak.items[-1].skill_id == TARGET_SKILL
    assert after_streak.items[-1].role == "main"


def test_difficulty_advances_after_confident_success_without_waiting_for_low_uncertainty():
    now = datetime.now(timezone.utc)
    state = SkillState(
        skill_id="MAT-FRA-006",
        ability=0.46,
        uncertainty=0.85,
        observations=3,
        working_difficulty=3,
        last_practiced_at=now,
    )
    profile = StudentProfile(
        user_id="student-ready-to-advance",
        skills={state.skill_id: state},
        updated_at=now,
    )

    assert DifficultySelector().select_difficulty(state.skill_id, profile) == 4.0


def test_domain_plan_interleaves_a_short_review_after_main_tasks():
    now = datetime.now(timezone.utc)
    profile = StudentProfile(
        user_id="student-with-mastered-fractions",
        updated_at=now,
        skills={
            skill_id: SkillState(
                skill_id=skill_id,
                ability=4.0,
                uncertainty=0.3,
                observations=5,
                working_difficulty=3,
                last_practiced_at=now,
            )
            for skill_id in ("MAT-FRA-001", "MAT-FRA-002")
        },
    )
    planner = PlannerService(JsonSkillNavigator(load_production_skills()))

    plan = planner.create_plan(
        PlannerInput(
            user_id=profile.user_id,
            selected_domain="ulamki",
            student_profile=profile,
            curriculum_context=CurriculumContext(
                grade=8,
                education_stage="primary school",
                curriculum_level="STANDARD",
            ),
        )
    )

    main_item = next(item for item in plan.items if item.role == "main")
    review_item = next(item for item in plan.items if item.role == "review")
    assert main_item.task_allocations[0].count == 2
    assert review_item.task_allocations[0].count == 2
    assert review_item.skill_id in {"MAT-FRA-001", "MAT-FRA-002"}
    integration_item = next(item for item in plan.items if item.role == "integration")
    assert integration_item.task_allocations[0].count == 2
    assert len(integration_item.skill_ids) == 2
