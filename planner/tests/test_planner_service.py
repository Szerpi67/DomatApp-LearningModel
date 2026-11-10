from datetime import datetime

import pytest
from unittest.mock import MagicMock
from profiler.models.student_profile import StudentProfile
from profiler.models.skill_state import SkillState
from planner.models.planner_input import PlannerInput, CurriculumContext
from planner.services.planner_service import PlannerService


def test_planner_service_blocked_skill():
    # Mock navigatora zgłasza, że skill nie istnieje
    navigator = MagicMock()
    navigator.has_skill.return_value = False

    service = PlannerService(skill_navigator=navigator)
    planner_input = PlannerInput(
        user_id="u1",
        target_skill_id="nieznany_skill",
        student_profile=StudentProfile(user_id="u1", skills={}, updated_at=datetime.now()),
        curriculum_context=CurriculumContext(grade=4, education_stage="szkoła podstawowa")
    )

    plan = service.create_plan(planner_input)

    assert plan.status == "blocked"
    assert len(plan.items) == 0
    assert "nie istnieje" in plan.reason


def test_planner_service_creates_ready_plan_with_remediation():
    # Mock navigatora potwierdza cel i zwraca jedno wymaganie wstępne
    navigator = MagicMock()
    navigator.has_skill.return_value = True
    navigator.get_prerequisites.return_value = ["prereq_wspolny_mianownik"]

    # Profil ucznia ze słabym opanowaniem wymagania (niskie ability / mastery)
    profile = StudentProfile(user_id="u1", skills={
        "prereq_wspolny_mianownik": SkillState(
            skill_id="prereq_wspolny_mianownik",
            ability=-1.5,
            uncertainty=0.5,
            working_difficulty=2,
            last_practiced_at=datetime.now()
        )
    }, updated_at=datetime.now())

    service = PlannerService(skill_navigator=navigator)
    planner_input = PlannerInput(
        user_id="u1",
        target_skill_id="dodawanie_ulamkow",
        student_profile=profile,
        curriculum_context=CurriculumContext(grade=4, education_stage="szkoła podstawowa")
    )

    plan = service.create_plan(planner_input)

    assert plan.status == "ready"
    assert len(plan.items) == 2  # 1x remediacja + 1x cel główny

    # Pierwszy element to remediacja braku
    assert plan.items[0].role == "remediation"
    assert plan.items[0].skill_id == "prereq_wspolny_mianownik"

    # Drugi element to cel główny ucznia
    assert plan.items[1].role == "main"
    assert plan.items[1].skill_id == "dodawanie_ulamkow"