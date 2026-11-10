from typing import List, Tuple
from planner.models.planner_input import PlannerInput
from planner.core.readiness_evaluator import ReadinessEvaluator
from planner.core.difficulty_selector import DifficultySelector
from planner.models.learning_plan import PlanItem, TaskAllocation


class PlanningPolicy:
    def __init__(self, readiness_evaluator: ReadinessEvaluator, difficulty_selector: DifficultySelector):
        self.readiness_evaluator = readiness_evaluator
        self.difficulty_selector = difficulty_selector

    def build_items(
            self,
            planner_input: PlannerInput,
            prerequisites: List[str]
    ) -> Tuple[List[PlanItem], str]:
        items: List[PlanItem] = []
        evaluated_prereqs = []

        # 1. Ewaluacja każdego wymagania wstępnego
        for prereq_id in prerequisites:
            status = self.readiness_evaluator.evaluate(prereq_id, planner_input.student_profile)

            # Pobieramy aktualne mastery, żeby posortować według wielkości braku
            skill_state = planner_input.student_profile.skills.get(prereq_id)
            mastery = getattr(skill_state, "difficulty_mastery", 0.0) if skill_state else 0.0

            evaluated_prereqs.append({
                "skill_id": prereq_id,
                "status": status,
                "mastery": mastery
            })

        # 2. Sortowanie braków: od najmniejszego mastery (największe dziury) do największego
        # Bierzemy pod uwagę te, które wymagają remediacji lub diagnostyki
        problematic_prereqs = [
            p for p in evaluated_prereqs if p["status"] in ("needs_remediation", "needs_diagnostic")
        ]
        problematic_prereqs.sort(key=lambda x: x["mastery"])

        # 3. Wplatanie elementów naprawczych/diagnostycznych do planu
        for p in problematic_prereqs:
            prereq_id = p["skill_id"]
            status = p["status"]
            diff = self.difficulty_selector.select_difficulty(prereq_id, planner_input.student_profile)

            if status == "needs_diagnostic":
                items.append(PlanItem(
                    skill_id=prereq_id,
                    role="diagnostic",
                    target_difficulty=diff,
                    material_scope=f"Diagnostyka wymagania {prereq_id}",
                    reason="Wysoka niepewność w profilu – wymagane zebranie dodatkowych danych."
                ))
            else:
                items.append(PlanItem(
                    skill_id=prereq_id,
                    role="remediation",
                    target_difficulty=diff,
                    material_scope=f"Ćwiczenia naprawcze dla {prereq_id}",
                    reason=f"Wykryto brak (niski poziom opanowania: {p['mastery']:.2f})."
                ))

        # 4. Dodanie celu głównego na koniec ścieżki
        target_id = planner_input.target_skill_id
        target_diff = self.difficulty_selector.select_difficulty(target_id, planner_input.student_profile)
        items.append(PlanItem(
            skill_id=target_id,
            role="main",
            target_difficulty=target_diff,
            material_scope=f"Klasa {planner_input.curriculum_context.grade} - {planner_input.curriculum_context.curriculum_level}",
            reason="Cel wybrany na podstawie działu, programu i profilu ucznia."
        ))

        task_count_by_role = {"diagnostic": 1, "remediation": 2, "main": 2}
        for item in items:
            item.task_allocations = [
                TaskAllocation(
                    task_type="closed_single_choice",
                    count=task_count_by_role[item.role],
                )
            ]

        remediation_count = len([i for i in items if i.role == "remediation"])
        diagnostic_count = len([i for i in items if i.role == "diagnostic"])
        summary_reason = f"Utworzono plan dla celu {target_id}. Wpleciono {remediation_count} kroków naprawczych i {diagnostic_count} diagnostycznych."

        return items, summary_reason
