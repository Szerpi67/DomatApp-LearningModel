import numpy as np
from profiler.core.irt_model import calculate_difficulty_mastery
from profiler.models.student_profile import StudentProfile
from planner.models.skill_potential import SkillPotential


class DifficultySelector:
    # Use hysteresis so one unexpected answer does not move the assigned level.
    escalation_min_observations = 3
    escalation_mastery_threshold = 0.72
    downgrade_mastery_threshold = 0.55
    escalation_uncertainty_threshold = 1.0

    def select_difficulty(
            self,
            skill_id: str,
            student_profile: StudentProfile,
            potential: SkillPotential = None
    ) -> float:
        skill_state = student_profile.skills.get(skill_id)

        if not skill_state or skill_state.working_difficulty is None:
            # Domyślny poziom bazowy przy braku danych
            base_diff = 3.0
        else:
            # Wykorzystujemy working_difficulty zapisanego w SkillState
            base_diff = float(skill_state.working_difficulty)

        # Jeżeli mamy wysoki wskaźnik pewności potencjału, delikatnie korygujemy
        if potential and potential.confidence > 0.7:
            base_diff = 0.8 * base_diff + 0.2 * potential.estimated_level

        if (
            skill_state is not None
            and skill_state.observations >= self.escalation_min_observations
        ):
            current_level = int(np.clip(round(base_diff), 1, 10))
            mastery = calculate_difficulty_mastery(
                skill_state.ability,
                skill_state.uncertainty,
            )[current_level]

            if (
                skill_state.uncertainty <= self.escalation_uncertainty_threshold
                and mastery >= self.escalation_mastery_threshold
            ):
                base_diff = min(10.0, current_level + 1.0)
            elif (
                skill_state.uncertainty <= self.escalation_uncertainty_threshold
                and mastery <= self.downgrade_mastery_threshold
            ):
                base_diff = max(1.0, current_level - 1.0)

        return float(np.round(np.clip(base_diff, 1.0, 10.0), 1))
