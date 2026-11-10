import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from planner.models.planner_input import CurriculumContext, PlannerInput
from planner.services.planner_service import PlannerService
from profiler.models.skill_state import SkillState
from profiler.models.student_profile import StudentProfile
from profiler.navigator.skill_navigator import SkillNavigator
from profiler.repositories.mongo_profile_repository import MongoProfileRepository
from profiler.services.profiler_service import ProfilerService
from pymongo.errors import PyMongoError
from task_generator.cache import CachedTaskService
from task_generator.models import TaskGenerationRequest
from task_generator.profiler_adapter import build_learning_event
from task_generator.repositories.mongo_task_store import MongoTaskStore
from task_generator.service import TaskGenerationError, TaskGeneratorService, TaskPresentationService


ROOT = Path(__file__).resolve().parent
SKILLS_DIR = ROOT / "data_files" / "skills"
SELECTED_DOMAIN = "ulamki"
USER_ID = "demo-student"


def load_skill_navigator() -> SkillNavigator:
    skills = []
    for path in sorted(SKILLS_DIR.rglob("*.json")):
        with path.open(encoding="utf-8") as skill_file:
            skills.extend(json.load(skill_file)["skills"])
    if not skills:
        raise ValueError(f"No skill definitions found in {SKILLS_DIR}")
    return SkillNavigator.from_skill_records(skills)


def create_demo_profile() -> StudentProfile:
    """Assume division is already known so the demo starts with fraction skills."""
    now = datetime.now(timezone.utc)
    return StudentProfile(
        user_id=USER_ID,
        skills={
            "MAT-ARI-002": SkillState(
                skill_id="MAT-ARI-002",
                ability=5.0,
                uncertainty=0.2,
                observations=10,
                working_difficulty=3,
                last_practiced_at=now,
            )
        },
        updated_at=now,
    )


def ask_for_choice(options):
    labels = "ABCD"
    label_to_option = dict(zip(labels, options))
    while True:
        last_label = labels[len(options) - 1]
        choice = input(f"Twoja odpowiedź (A-{last_label}): ").strip().upper()
        if choice in label_to_option:
            return label_to_option[choice]
        print("Wpisz jedną z liter: A, B, C albo D.")


def run_learning_session(store: MongoTaskStore, repository: MongoProfileRepository):
    navigator = load_skill_navigator()
    planner = PlannerService(navigator)
    generator = TaskGeneratorService(navigator)
    cached_tasks = CachedTaskService(generator, store)
    presenter = TaskPresentationService(key_store=store)
    profiler = ProfilerService(repository)
    if repository.get(USER_ID) is None:
        repository.save(create_demo_profile())

    context = CurriculumContext(
        grade=int(os.getenv("EDUBEE_GRADE", "8")),
        education_stage="primary school",
        curriculum_level="STANDARD",
    )
    correct_count = 0
    session_number = 0
    results_by_skill = {}
    pending_tasks = None
    pending_plan_items = None

    skill_names = {
        skill_id: skill.get("name", skill_id)
        for skill_id, skill in navigator.skills.items()
    }
    print("\nEduBee — demonstracyjna sesja: dział ułamków")
    print("Planner dobiera kolejne umiejętności z działu na podstawie profilu.")
    print("Założenie: uczeń zna już podstawowe dzielenie. Wpisz Ctrl+C, aby przerwać.\n")

    while True:
        if pending_tasks is None:
            if pending_plan_items is None:
                profile = repository.get(USER_ID)
                plan = planner.create_plan(
                    PlannerInput(
                        user_id=USER_ID,
                        selected_domain=SELECTED_DOMAIN,
                        student_profile=profile,
                        curriculum_context=context,
                    )
                )
                if plan.status == "blocked" or not plan.items:
                    print(f"Nie uda?o si? przygotowa? planu: {plan.reason}")
                    break
                store.save_learning_plan(plan, USER_ID, context)
                pending_plan_items = iter(plan.items)

            try:
                plan_item = next(pending_plan_items)
            except StopIteration:
                pending_plan_items = None
                continue
            target_name = skill_names.get(plan.target_skill_id, plan.target_skill_id)
            current_name = ", ".join(
                skill_names.get(skill_id, skill_id)
                for skill_id in plan_item.skill_ids
            )
            store.touch_learning_plan(plan.plan_id)
            pending_tasks = iter(cached_tasks.stream_tasks(
                TaskGenerationRequest(
                    plan_id=plan.plan_id,
                    plan_item=plan_item,
                    curriculum_context=context,
                ),
                student_id=USER_ID,
            ))

        try:
            task = next(pending_tasks)
        except StopIteration:
            pending_tasks = None
            continue
        session_number += 1
        presented = presenter.present(task)

        print(
            f"Cel: {target_name} ({plan.target_skill_id})\n"
            f"Krok planu: {current_name} ({plan_item.role})\n"
            f"Zadanie {session_number} · trudność {task.difficulty}/10"
        )
        print(presented.statement)
        labels = "abcd"
        for label, option in zip(labels, presented.options):
            print(f"  {label}) {option.text}")

        started_at = monotonic()
        selected_option = ask_for_choice(presented.options)
        response_time_ms = round((monotonic() - started_at) * 1000)
        result = presenter.grade(presented.presentation_id, selected_option.id)
        for related_skill_id in task.skill_ids:
            skill_stats = results_by_skill.setdefault(
                related_skill_id, {"correct": 0, "total": 0}
            )
            skill_stats["total"] += 1
            if result.correct:
                skill_stats["correct"] += 1
        if result.correct:
            correct_count += 1
            print("Dobrze!\n")
        else:
            correct_option = next(
                option
                for option in presented.options
                if presenter.grade(presented.presentation_id, option.id).correct
            )
            print(f"Tym razem nie. Poprawna odpowiedź: {correct_option.text}\n")

        print("Rozwiązanie:")
        for step in task.solution_steps:
            print(f"  • {step}")
        print(task.explanation)

        event = build_learning_event(
            user_id=USER_ID,
            session_id=f"demo-session-{USER_ID}",
            task=task,
            presentation=presented,
            result=result,
            response_time_ms=response_time_ms,
        )
        updated_profile = profiler.process_event(event)
        skill = updated_profile.skills[plan_item.skill_id]
        print(
            f"Profil: ability={skill.ability:.2f}, "
            f"niepewność={skill.uncertainty:.2f}, "
            f"obserwacje={skill.observations}, "
            f"poziom roboczy={skill.working_difficulty}/10\n"
        )

        if input("Czy chcesz kolejne zadanie? (t/n): ").strip().casefold() not in {
            "t",
            "tak",
            "y",
            "yes",
        }:
            break

    final_profile = repository.get(USER_ID)
    print("\nPodsumowanie sesji")
    print(f"Zadania: {session_number}")
    if session_number:
        print(
            f"Poprawne: {correct_count}/{session_number} "
            f"({correct_count / session_number:.0%})"
        )
    for skill_id, stats in results_by_skill.items():
        state = final_profile.skills[skill_id]
        name = skill_names.get(skill_id, skill_id)
        print(
            f"- {name}: sesja {stats['correct']}/{stats['total']} poprawnych; "
            f"profil łącznie: obserwacje={state.observations}, "
            f"ability={state.ability:.2f}, uncertainty={state.uncertainty:.2f}, "
            f"poziom={state.working_difficulty}/10"
        )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        store = MongoTaskStore.from_environment()
        profiles = MongoProfileRepository.from_environment()
        try:
            store.ping()
            profiles.ping()
            run_learning_session(store, profiles)
        finally:
            store.close()
            profiles.close()
    except (KeyboardInterrupt, EOFError):
        print("\nSesja przerwana.")
    except TaskGenerationError as error:
        print(f"Nie udało się przygotować zadania ({error.code}): {error}")
    except PyMongoError as error:
        print(
            "Nie można połączyć się z MongoDB. Sprawdź, czy usługa działa "
            "oraz ustawienia MONGODB_URI. Szczegóły: "
            f"{error}"
        )
