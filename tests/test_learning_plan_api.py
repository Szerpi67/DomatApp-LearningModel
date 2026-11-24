from datetime import datetime, timezone
from unittest.mock import Mock

import api
from planner.models.learning_plan import LearningPlan
from planner.models.planner_input import CurriculumContext
from profiler.models.student_profile import StudentProfile


def test_learning_plan_api_persists_grade_for_future_diagnostics(monkeypatch):
    profile = StudentProfile(
        user_id="student-1",
        updated_at=datetime.now(timezone.utc),
    )
    profiles = Mock()
    profiles.get.return_value = profile
    planner = Mock()
    planner.create_plan.return_value = LearningPlan(
        plan_id="plan-1",
        target_skill_id="fraction-skill",
        status="needs_diagnostic",
        reason="Initial domain assessment.",
    )
    task_store = Mock()
    monkeypatch.setattr(api.app.state, "profile_repository", profiles, raising=False)
    monkeypatch.setattr(api.app.state, "planner", planner, raising=False)
    monkeypatch.setattr(api.app.state, "task_store", task_store, raising=False)

    plan = api.create_learning_plan(
        api.LearningPlanRequest(
            student_id="student-1",
            selected_domain="fractions",
            curriculum_context=CurriculumContext(
                grade=4, education_stage="primary school"
            ),
        )
    )

    assert plan.status == "needs_diagnostic"
    assert profile.last_grade == 4
    profiles.save.assert_called_once_with(profile)
    task_store.save_learning_plan.assert_called_once()
