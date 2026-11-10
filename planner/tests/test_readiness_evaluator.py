from datetime import datetime

import pytest
from profiler.models.student_profile import StudentProfile
from profiler.models.skill_state import SkillState
from planner.core.readiness_evaluator import ReadinessEvaluator


def test_evaluator_needs_diagnostic_for_missing_skill():
    profile = StudentProfile(user_id="u1", skills={}, updated_at=datetime.now())
    evaluator = ReadinessEvaluator()

    status = evaluator.evaluate("brakujacy_skill", profile)
    assert status == "needs_diagnostic"


def test_evaluator_needs_diagnostic_for_high_uncertainty():
    profile = StudentProfile(user_id="u1", skills={
        "skill_1": SkillState(skill_id="skill_1", ability=0.5, uncertainty=2.0, last_practiced_at=datetime.now())  # niskie ability da niskie mastery
    }, updated_at=datetime.now())
    evaluator = ReadinessEvaluator(uncertainty_threshold=1.5)

    status = evaluator.evaluate("skill_1", profile)
    assert status == "needs_diagnostic"


def test_evaluator_needs_remediation_for_low_mastery():
    profile = StudentProfile(user_id="u1", skills={
        "skill_1": SkillState(skill_id="skill_1", ability=-2.0, uncertainty=0.5, last_practiced_at=datetime.now())  # niskie ability da niskie mastery
    }, updated_at=datetime.now())
    evaluator = ReadinessEvaluator(mastery_threshold=0.7, uncertainty_threshold=1.5)

    status = evaluator.evaluate("skill_1", profile)
    assert status == "needs_remediation"