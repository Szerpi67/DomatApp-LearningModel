from __future__ import annotations

from datetime import datetime, timezone
import os

from pymongo import ASCENDING, MongoClient

from profiler.models.learning_event import LearningEvent
from profiler.models.student_profile import StudentProfile


class MongoProfileRepository:
    """Persist learner profiles and the learning events used to update them."""

    def __init__(
        self,
        uri: str = "mongodb://localhost:27017",
        database_name: str = "domat-test",
        *,
        timeout_ms: int = 3000,
    ):
        self.client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        self.database = self.client[database_name]
        self.profiles = self.database["student_profiles"]
        self.events = self.database["learning_events"]
        self.events.create_index(
            [("user_id", ASCENDING), ("timestamp", ASCENDING)],
            name="learner_event_timeline",
        )

    @classmethod
    def from_environment(cls) -> "MongoProfileRepository":
        return cls(
            uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
            database_name=os.getenv("MONGODB_DATABASE", "domat-test"),
            timeout_ms=int(os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "3000")),
        )

    def ping(self) -> None:
        self.client.admin.command("ping")

    def get(self, user_id: str) -> StudentProfile | None:
        document = self.profiles.find_one({"_id": user_id}, {"profile": 1})
        if document is None:
            return None
        return StudentProfile.model_validate(document["profile"])

    def save(self, profile: StudentProfile) -> None:
        now = datetime.now(timezone.utc)
        self.profiles.replace_one(
            {"_id": profile.user_id},
            {
                "_id": profile.user_id,
                "profile": profile.model_dump(mode="json"),
                "updated_at": now,
            },
            upsert=True,
        )

    def is_event_processed(self, event_id: str) -> bool:
        return self.events.find_one(
            {"_id": event_id, "status": "processed"}, {"_id": 1}
        ) is not None

    def mark_event_processed(
        self, event_id: str, event: LearningEvent | None = None
    ) -> None:
        existing = self.events.find_one({"_id": event_id}, {"event": 1})
        event_payload = (
            event.model_dump(mode="json")
            if event is not None
            else (existing or {}).get("event")
        )
        if event_payload is None:
            raise ValueError("A learning event is required for MongoDB persistence")
        self.events.replace_one(
            {"_id": event_id},
            {
                "_id": event_id,
                "user_id": event.user_id if event else event_payload["user_id"],
                "timestamp": event.timestamp if event else event_payload["timestamp"],
                "status": "processed",
                "event": event_payload,
                "processed_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )

    def get_event(self, event_id: str) -> LearningEvent | None:
        document = self.events.find_one({"_id": event_id}, {"event": 1})
        return LearningEvent.model_validate(document["event"]) if document else None

    def close(self) -> None:
        self.client.close()
