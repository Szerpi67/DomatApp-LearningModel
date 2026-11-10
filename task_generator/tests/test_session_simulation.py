r"""Offline end-to-end learner session simulation with a printed transcript.

Run with:
    .venv\Scripts\python.exe -m pytest -s -q task_generator\tests\test_session_simulation.py
"""

from datetime import datetime, timedelta, timezone
import json
import random
import sys

from planner.models.planner_input import CurriculumContext, PlannerInput
from planner.services.planner_service import PlannerService
from profiler.models.skill_state import SkillState
from profiler.models.student_profile import StudentProfile
from profiler.navigator.skill_navigator import SkillNavigator
from profiler.repositories.profile_repository import InMemoryProfileRepository
from profiler.services.profiler_service import ProfilerService
from task_generator.models import AnswerOption, TaskGenerationRequest, TaskTemplate
from task_generator.profiler_adapter import build_learning_event
from task_generator.service import TaskGeneratorService, TaskPresentationService


class SessionSkillNavigator:
    def __init__(self):
        self.skills = [
            {
                "id": skill_id,
                "name": name,
                "tags": ["fractions"],
                "prerequisites": [],
                "short_description": name,
                "curriculum": {
                    "introduced_in_grade": 4,
                    "school_type": "primary",
                    "level": "STANDARD",
                },
            }
            for skill_id, name in (
                ("MAT-FRA-101", "Rozumienie ułamka"),
                ("MAT-FRA-102", "Zapisywanie ułamków"),
                ("MAT-FRA-103", "Porównywanie ułamków"),
            )
        ]
        self._by_id = {skill["id"]: skill for skill in self.skills}

    def get_skills(self):
        return self.skills

    def has_skill(self, skill_id):
        return skill_id in self._by_id

    def get_prerequisites(self, skill_id):
        return self._by_id[skill_id]["prerequisites"]


def _task_for(request, sequence_number):
    skill_id = request.plan_item.skill_id
    if skill_id == "MAT-FRA-103":
        left, right = 2 + sequence_number, 5 + sequence_number
        statement = f"Porównaj ułamki {left}/8 i {right}/8."
        correct = f"{left}/8 < {right}/8"
        distractors = [
            f"{left}/8 > {right}/8",
            f"{left}/8 = {right}/8",
            f"{right}/8 < {left}/8",
        ]
        solution = [
            "Porównaj liczniki, bo mianowniki są równe.",
            f"{left} jest mniejsze od {right}, więc pierwszy ułamek jest mniejszy.",
        ]
    else:
        numerator = 2 + sequence_number % 5
        statement = f"Zapisz ułamek: {numerator} z 8 równych części."
        correct = f"{numerator}/8"
        distractors = [
            f"{8 - numerator}/8",
            f"8/{numerator}",
            f"{numerator}/9",
        ]
        solution = [
            f"Liczba zaznaczonych części, {numerator}, jest licznikiem.",
            "Liczba wszystkich części, 8, jest mianownikiem.",
        ]
    return TaskTemplate(
        task_id=f"simulated-task-{sequence_number}",
        plan_id=request.plan_id,
        skill_id=skill_id,
        skill_ids=request.plan_item.skill_ids,
        subskill_ids=request.plan_item.subskill_ids,
        subskills_by_skill=request.plan_item.subskills_by_skill,
        difficulty=round(request.plan_item.target_difficulty),
        requested_difficulty=request.plan_item.target_difficulty,
        curriculum_context=request.curriculum_context,
        material_scope=request.plan_item.material_scope,
        statement=statement,
        answers=[
            AnswerOption(id=f"answer-{index}", text=text)
            for index, text in enumerate([correct, *distractors], start=1)
        ],
        correct_answers=["answer-1"],
        solution_steps=solution,
        explanation="Użyj licznika i mianownika, aby zapisać lub porównać ułamek.",
        generation_seed=sequence_number,
        validation_checks=["session_simulation"],
        generator_version="simulation",
    )


def test_simulated_session_prints_questions_answers_and_profile_updates(monkeypatch):
    navigator = SessionSkillNavigator()
    planner = PlannerService(navigator)
    generator = TaskGeneratorService(navigator)
    sequence = {"value": 0}

    def offline_task_stream(request, seed=None):
        count = sum(allocation.count for allocation in request.plan_item.task_allocations)
        for _ in range(count):
            sequence["value"] += 1
            yield _task_for(request, sequence["value"])

    monkeypatch.setattr(generator, "generate_batch_stream", offline_task_stream)
    presenter = TaskPresentationService(rng=random.Random(23))
    profiles = InMemoryProfileRepository()
    profiler = ProfilerService(profiles)
    now = datetime.now(timezone.utc)
    profiles.save(
        StudentProfile(
            user_id="simulated-learner",
            updated_at=now,
            skills={
                skill_id: SkillState(
                    skill_id=skill_id,
                    ability=4.0,
                    uncertainty=0.3,
                    observations=5,
                    working_difficulty=3,
                    last_practiced_at=now - timedelta(days=days_ago),
                )
                for skill_id, days_ago in (
                    ("MAT-FRA-101", 14),
                    ("MAT-FRA-102", 7),
                )
            },
        )
    )

    context = CurriculumContext(
        grade=8,
        education_stage="primary school",
        curriculum_level="STANDARD",
    )
    transcript = []
    plan_roles = []
    for plan_number in range(1, 3):
        profile_before_plan = profiles.get("simulated-learner")
        plan = planner.create_plan(
            PlannerInput(
                user_id="simulated-learner",
                selected_domain="fractions",
                student_profile=profile_before_plan,
                curriculum_context=context,
            )
        )
        assert plan.status == "ready"
        plan_roles.append([item.role for item in plan.items])

        for item in plan.items:
            request = TaskGenerationRequest(
                plan_id=plan.plan_id,
                plan_item=item,
                curriculum_context=context,
            )
            generated = generator.generate_batch_stream(request)
            for task in generated:
                presentation = presenter.present(task)
                correct_option = next(
                    option
                    for option in presentation.options
                    if presenter.grade(presentation.presentation_id, option.id).correct
                )
                if len(transcript) == 1:
                    selected_option = next(
                        option
                        for option in presentation.options
                        if option.id != correct_option.id
                    )
                else:
                    selected_option = correct_option
                result = presenter.grade(
                    presentation.presentation_id, selected_option.id
                )
                before = profiles.get("simulated-learner").skills.get(task.skill_id)
                event = build_learning_event(
                    user_id="simulated-learner",
                    session_id="offline-session-simulation",
                    task=task,
                    presentation=presentation,
                    result=result,
                    response_time_ms=700 + 100 * len(transcript),
                )
                updated_profile = profiler.process_event(event)
                after = updated_profile.skills[task.skill_id]
                option_labels = "ABCD"
                transcript.append(
                    {
                        "number": len(transcript) + 1,
                        "plan": plan_number,
                        "role": item.role,
                        "skill": task.skill_id,
                        "difficulty": task.difficulty,
                        "question": task.statement,
                        "options": {
                            option_labels[index]: option.text
                            for index, option in enumerate(presentation.options)
                        },
                        "learner_answer": next(
                            option_labels[index]
                            for index, option in enumerate(presentation.options)
                            if option.id == selected_option.id
                        ),
                        "selected_text": selected_option.text,
                        "correct_text": correct_option.text,
                        "correct": result.correct,
                        "profile_update": {
                            "ability_before": before.ability if before else 0.0,
                            "ability_after": after.ability,
                            "uncertainty_after": after.uncertainty,
                            "observations": after.observations,
                            "working_difficulty": after.working_difficulty,
                        },
                    }
                )

    assert len(transcript) == 12
    assert all("review" in roles and "integration" in roles for roles in plan_roles)
    expected_roles_per_plan = [
        "main", "main", "review", "review", "integration", "integration"
    ]
    assert [entry["role"] for entry in transcript] == expected_roles_per_plan * 2
    assert transcript[1]["correct"] is False

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    final_profile = profiles.get("simulated-learner")
    summary = {
        "tasks": len(transcript),
        "correct": sum(entry["correct"] for entry in transcript),
        "accuracy": sum(entry["correct"] for entry in transcript) / len(transcript),
        "skills": {
            skill_id: state.model_dump(mode="json")
            for skill_id, state in final_profile.skills.items()
        },
        "plan_roles": plan_roles,
    }
    print("\nSIMULATED SESSION REPORT")
    print(json.dumps({"summary": summary, "transcript": transcript}, ensure_ascii=False, indent=2))
