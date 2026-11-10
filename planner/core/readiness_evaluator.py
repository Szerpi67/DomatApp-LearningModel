from typing import Literal

from profiler.core import irt_model
from profiler.models.student_profile import StudentProfile

ReadinessStatus = Literal["mastered", "needs_remediation", "needs_diagnostic"]


class ReadinessEvaluator:
    def __init__(self, mastery_threshold: float = 0.7, uncertainty_threshold: float = 1.5):
        self.mastery_threshold = mastery_threshold
        self.uncertainty_threshold = uncertainty_threshold

    def evaluate(self, skill_id: str, student_profile: StudentProfile) -> ReadinessStatus:
        skill_state = student_profile.skills.get(skill_id)
        if not skill_state:
            return "needs_diagnostic"

        # Jeśli niepewność jest zbyt wysoka, najpierw diagnostyka
        if getattr(skill_state, "uncertainty", 0.0) > self.uncertainty_threshold:
            return "needs_diagnostic"

        # Obliczamy słownik mastery dla wszystkich poziomów (1-10)
        mastery_dict = irt_model.calculate_difficulty_mastery(skill_state.ability, skill_state.uncertainty)

        # Pobieramy mastery dla konkretnego poziomu working_difficulty ucznia (domyślnie 1, jeśli brak)
        current_level = getattr(skill_state, "working_difficulty", 1)
        mastery = mastery_dict.get(current_level, 0.0)

        # Teraz możemy bezpiecznie porównać float z progiem
        if mastery >= self.mastery_threshold:
            return "mastered"
        else:
            return "needs_remediation"