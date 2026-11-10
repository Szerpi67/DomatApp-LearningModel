from copy import deepcopy

from profiler.models.student_profile import StudentProfile


class InMemoryProfileRepository:
    def __init__(self):
        self._profiles: dict[str, StudentProfile] = {}
        self._processed_events: set[str] = set()

    def get(self, user_id: str) -> StudentProfile | None:
        profile = self._profiles.get(user_id)
        return deepcopy(profile) if profile is not None else None

    def save(self, profile: StudentProfile) -> None:
        self._profiles[profile.user_id] = deepcopy(profile)

    def is_event_processed(self, event_id: str) -> bool:
        return event_id in self._processed_events

    def mark_event_processed(self, event_id: str, event=None) -> None:
        self._processed_events.add(event_id)
