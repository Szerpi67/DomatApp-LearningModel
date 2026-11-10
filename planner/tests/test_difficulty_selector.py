from datetime import datetime

import pytest
from profiler.models.student_profile import StudentProfile
from profiler.models.skill_state import SkillState
from planner.core.difficulty_selector import DifficultySelector
from planner.models.skill_potential import SkillPotential


def test_difficulty_selector_default_when_no_skill():
    profile = StudentProfile(user_id="u1", skills={}, updated_at=datetime.now())
    selector = DifficultySelector()

    diff = selector.select_difficulty("nowy_skill", profile)
    assert diff == 3.0  # Domyślny poziom bazowy przy braku danych


def test_difficulty_selector_uses_working_difficulty():
    profile = StudentProfile(user_id="u1", skills={
        "skill_1": SkillState(skill_id="skill_1", working_difficulty=6, last_practiced_at=datetime.now())
    }, updated_at=datetime.now())
    selector = DifficultySelector()

    diff = selector.select_difficulty("skill_1", profile)
    assert diff == 6.0


def test_difficulty_selector_applies_potential_when_high_confidence():
    profile = StudentProfile(user_id="u1", skills={
        "skill_1": SkillState(skill_id="skill_1", working_difficulty=4, last_practiced_at=datetime.now())
    }, updated_at=datetime.now())
    # Wysoka pewność potencjału (np. 0.9), szacowany poziom 8.0
    potential = SkillPotential(skill_id="skill_1", estimated_level=8.0, confidence=0.9)
    selector = DifficultySelector()

    diff = selector.select_difficulty("skill_1", profile, potential=potential)
    # Spodziewamy się delikatnego przesunięcia w stronę potencjału (0.8 * 4 + 0.2 * 8 = 4.8)
    assert diff == 4.8


def test_difficulty_selector_ignores_potential_when_low_confidence():
    profile = StudentProfile(user_id="u1", skills={
        "skill_1": SkillState(skill_id="skill_1", working_difficulty=4, last_practiced_at=datetime.now())
    }, updated_at=datetime.now())
    # Niska pewność potencjału (np. 0.4)
    potential = SkillPotential(skill_id="skill_1", estimated_level=9.0, confidence=0.4)
    selector = DifficultySelector()

    diff = selector.select_difficulty("skill_1", profile, potential=potential)
    # Potencjał nie powinien wpłynąć, zostaje working_difficulty = 4.0
    assert diff == 4.0