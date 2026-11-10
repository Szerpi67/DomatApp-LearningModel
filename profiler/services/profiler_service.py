
from datetime import datetime, timezone

from profiler.core.bayesian_updater import update
from profiler.models.learning_event import LearningEvent
from profiler.models.skill_state import SkillState, SubskillState
from profiler.models.student_profile import StudentProfile
class ProfilerService:
    def __init__(self, repository):
        self.repository = repository

    def process_event(self, event: LearningEvent) -> StudentProfile:
        # 1. Idempotency check
        if self.repository.is_event_processed(event.event_id):
            profile = self.repository.get(event.user_id)

            if profile is None:
                raise ValueError("Profile missing for processed event")

            return profile

        # 2. Get or initialize profile
        profile = self.repository.get(event.user_id)

        if profile is None:
            profile = StudentProfile(user_id=event.user_id, updated_at=datetime.now(timezone.utc))

        # 3. Update every skill contributing to an integrated task. Evidence is
        # weighted so one combined question is weaker evidence than a focused one.
        for task_skill in event.task.skills:
            skill_state = profile.skills.get(task_skill.skill_id)
            if skill_state is None:
                skill_state = SkillState(
                    skill_id=task_skill.skill_id,
                    last_practiced_at=event.timestamp,
                )
                profile.skills[task_skill.skill_id] = skill_state
            if not event.result.skipped:
                self._update_state(
                    skill_state,
                    event.task.difficulty,
                    event.result.correct,
                    task_skill.weight,
                    event.timestamp,
                )
            subskill_weight = task_skill.weight / max(1, len(task_skill.subskill_ids))
            for subskill_id in task_skill.subskill_ids:
                subskill_state = skill_state.subskills.get(subskill_id)
                if subskill_state is None:
                    subskill_state = SubskillState(last_practiced_at=event.timestamp)
                    skill_state.subskills[subskill_id] = subskill_state
                if not event.result.skipped:
                    self._update_state(
                        subskill_state,
                        event.task.difficulty,
                        event.result.correct,
                        subskill_weight,
                        event.timestamp,
                    )

        # 6. Update profile timestamp
        profile.updated_at = datetime.now(timezone.utc)

        # 7. Persist
        self.repository.save(profile)
        self.repository.mark_event_processed(event.event_id, event)

        return profile

    @staticmethod
    def _update_state(state, difficulty, correct, evidence_weight, timestamp):
        posterior_ability, posterior_uncertainty = update(
            ability=state.ability,
            uncertainty=state.uncertainty,
            difficulty=difficulty,
            correct=correct,
        )
        state.ability += evidence_weight * (posterior_ability - state.ability)
        state.uncertainty += evidence_weight * (
            posterior_uncertainty - state.uncertainty
        )
        state.observations += 1
        state.working_difficulty = difficulty
        state.last_practiced_at = timestamp
