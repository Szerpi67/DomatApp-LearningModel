from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

from pymongo import ASCENDING, MongoClient

from task_generator.models import TaskTemplate
from task_generator.quality import has_single_true_comparison, is_statement_well_formed


LEARNING_PLAN_RETENTION_DAYS = 90


class MongoTaskStore:
    """MongoDB task cache and presentation answer-key store."""

    def __init__(self, uri: str, database_name: str = "domat-test", *, timeout_ms: int = 3000):
        self.client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
        self.database = self.client[database_name]
        self.tasks = self.database["task_templates"]
        self.presentations = self.database["task_presentations"]
        self.task_history = self.database["learner_task_history"]
        self.learning_plans = self.database["learning_plans"]
        self.tasks.create_index(
            [("cache_key", ASCENDING), ("difficulty", ASCENDING), ("use_count", ASCENDING)],
            name="task_cache_lookup_v2",
        )
        self.tasks.create_index(
            [("skill_id", ASCENDING), ("task_type", ASCENDING), ("grade", ASCENDING), ("difficulty", ASCENDING)],
            name="task_metadata_lookup",
        )
        self.presentations.create_index(
            "expires_at", expireAfterSeconds=0, name="presentation_ttl"
        )
        self.learning_plans.update_many(
            {"expires_at": {"$exists": False}},
            {
                "$set": {
                    "expires_at": datetime.now(timezone.utc)
                    + timedelta(days=LEARNING_PLAN_RETENTION_DAYS)
                }
            },
        )
        self.learning_plans.create_index(
            "expires_at", expireAfterSeconds=0, name="learning_plan_ttl"
        )
        self.task_history.create_index(
            [("learner_key", ASCENDING), ("task_id", ASCENDING)],
            unique=True,
            name="learner_task_unique",
        )
        self.task_history.create_index(
            [("learner_key", ASCENDING), ("last_seen_at", ASCENDING)],
            name="learner_history_lookup",
        )

    @classmethod
    def from_environment(cls) -> "MongoTaskStore":
        uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        database_name = os.getenv("MONGODB_DATABASE", "domat-test")
        timeout_ms = int(os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "3000"))
        return cls(uri, database_name, timeout_ms=timeout_ms)

    def ping(self) -> None:
        self.client.admin.command("ping")

    def take_cached_tasks(
        self,
        cache_key: str,
        limit: int,
        *,
        desired_difficulty: int,
        learner_key: str | None = None,
    ) -> list[TaskTemplate]:
        tasks = []
        query = {
            "cache_key": cache_key,
            "quality_status": {"$ne": "rejected"},
            "difficulty": {
                "$gte": max(1, desired_difficulty - 1),
                "$lte": min(10, desired_difficulty),
            },
        }
        documents = list(
            self.tasks.find(query)
            .sort([("use_count", ASCENDING), ("created_at", ASCENDING)])
            .limit(max(limit * 20, 100))
        )
        if not documents:
            return tasks

        quality_checked_documents = []
        for document in documents:
            task_payload = document["task"]
            option_texts = [answer["text"] for answer in task_payload["answers"]]
            correct_texts = [
                answer["text"] for answer in task_payload["answers"]
                if answer["id"] in task_payload["correct_answers"]
            ]
            comparison_valid = has_single_true_comparison(option_texts, correct_texts)
            if (
                not is_statement_well_formed(task_payload["statement"])
                or comparison_valid is False
            ):
                self.tasks.update_one(
                    {"_id": document["_id"]},
                    {
                        "$set": {
                            "quality_status": "rejected",
                            "quality_reason": "invalid_statement_or_multiple_correct_comparisons",
                            "quality_checked_at": datetime.now(timezone.utc),
                        }
                    },
                )
                continue
            quality_checked_documents.append(document)
        documents = quality_checked_documents
        if not documents:
            return tasks

        task_ids = [document["task"]["task_id"] for document in documents]
        history = {}
        if learner_key:
            history = {
                item["task_id"]: item["last_seen_at"]
                for item in self.task_history.find(
                    {"learner_key": learner_key, "task_id": {"$in": task_ids}},
                    {"task_id": 1, "last_seen_at": 1},
                )
            }
        content_groups = {}
        for document in documents:
            task_payload = document["task"]
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "statement": " ".join(task_payload["statement"].split()).casefold(),
                        "answers": sorted(
                            " ".join(answer["text"].split()).casefold()
                            for answer in task_payload["answers"]
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            content_groups.setdefault(fingerprint, []).append(document)

        distinct_documents = []
        for group in content_groups.values():
            previously_seen = [
                document for document in group
                if document["task"]["task_id"] in history
            ]
            if previously_seen:
                representative = max(
                    previously_seen,
                    key=lambda document: history[document["task"]["task_id"]],
                )
                last_seen = history[representative["task"]["task_id"]]
            else:
                representative = min(
                    group,
                    key=lambda document: (
                        document.get("use_count", 0), document["created_at"]
                    ),
                )
                last_seen = datetime.min.replace(tzinfo=timezone.utc)
            distinct_documents.append((representative, bool(previously_seen), last_seen))

        distinct_documents.sort(
            key=lambda item: (
                item[1],
                item[2],
                abs(item[0]["difficulty"] - desired_difficulty),
                item[0].get("use_count", 0),
                item[0]["created_at"],
            )
        )

        now = datetime.now(timezone.utc)
        for document, _, _ in distinct_documents[:limit]:
            task_id = document["task"]["task_id"]
            self.tasks.update_one(
                {"_id": document["_id"]},
                {"$inc": {"use_count": 1}, "$set": {"last_used_at": now}},
            )
            if learner_key:
                self.task_history.update_one(
                    {"learner_key": learner_key, "task_id": task_id},
                    {
                        "$set": {
                            "cache_key": cache_key,
                            "last_seen_at": now,
                        },
                        "$setOnInsert": {"first_seen_at": now},
                        "$inc": {"times_seen": 1},
                    },
                    upsert=True,
                )
            tasks.append(TaskTemplate.model_validate(document["task"]))
        return tasks

    def count_cached_tasks(self, cache_key: str, *, difficulty: int | None = None) -> int:
        query = {"cache_key": cache_key, "quality_status": {"$ne": "rejected"}}
        if difficulty is not None:
            query["difficulty"] = difficulty
        return self.tasks.count_documents(query)

    def get_task(self, task_id: str) -> TaskTemplate | None:
        document = self.tasks.find_one({"task.task_id": task_id}, {"task": 1})
        return TaskTemplate.model_validate(document["task"]) if document else None

    def save_learning_plan(self, plan, user_id: str, curriculum_context) -> None:
        now = datetime.now(timezone.utc)
        payload = plan.model_dump(mode="json")
        self.learning_plans.replace_one(
            {"_id": plan.plan_id},
            {
                "_id": plan.plan_id,
                "user_id": user_id,
                "plan": payload,
                "curriculum_context": curriculum_context.model_dump(mode="json"),
                "task_assignments": [],
                "created_at": now,
                "updated_at": now,
                "expires_at": now + timedelta(days=LEARNING_PLAN_RETENTION_DAYS),
            },
            upsert=True,
        )

    def touch_learning_plan(self, plan_id: str) -> None:
        now = datetime.now(timezone.utc)
        active_plan = {
            "_id": plan_id,
            "$or": [
                {"expires_at": {"$gt": now}},
                {"expires_at": {"$exists": False}},
            ],
        }
        self.learning_plans.update_one(
            active_plan,
            {
                "$set": {
                    "updated_at": now,
                    "expires_at": now + timedelta(days=LEARNING_PLAN_RETENTION_DAYS),
                }
            },
        )

    def get_learning_plan(self, plan_id: str) -> dict | None:
        self.touch_learning_plan(plan_id)
        now = datetime.now(timezone.utc)
        active_plan = {
            "_id": plan_id,
            "$or": [
                {"expires_at": {"$gt": now}},
                {"expires_at": {"$exists": False}},
            ],
        }
        document = self.learning_plans.find_one(active_plan)
        if document is None:
            return None
        return {
            "plan": document["plan"],
            "user_id": document["user_id"],
            "curriculum_context": document["curriculum_context"],
            "task_assignments": document.get("task_assignments", []),
        }

    def add_plan_task(self, plan_id: str, item_index: int, task_id: str) -> None:
        now = datetime.now(timezone.utc)
        self.learning_plans.update_one(
            {
                "_id": plan_id,
                "$or": [
                    {"expires_at": {"$gt": now}},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {
                "$addToSet": {"task_assignments": {"item_index": item_index, "task_id": task_id}},
                "$set": {
                    "updated_at": now,
                    "expires_at": now + timedelta(days=LEARNING_PLAN_RETENTION_DAYS),
                },
            },
        )

    def get_plan_tasks(self, plan_id: str, item_index: int | None = None) -> list[TaskTemplate]:
        document = self.learning_plans.find_one(
            {"_id": plan_id}, {"task_assignments": 1}
        )
        if document is None:
            return []
        assignments = document.get("task_assignments", [])
        if item_index is not None:
            assignments = [item for item in assignments if item["item_index"] == item_index]
        result = []
        for assignment in assignments:
            task = self.get_task(assignment["task_id"])
            if task is not None:
                result.append(task)
        return result

    def mark_task_seen(self, learner_key: str, cache_key: str, task_id: str) -> None:
        now = datetime.now(timezone.utc)
        self.task_history.update_one(
            {"learner_key": learner_key, "task_id": task_id},
            {
                "$set": {"cache_key": cache_key, "last_seen_at": now},
                "$setOnInsert": {"first_seen_at": now},
                "$inc": {"times_seen": 1},
            },
            upsert=True,
        )

    def save_task(
        self, cache_key: str, task: TaskTemplate, metadata: dict
    ) -> TaskTemplate | None:
        task_payload = task.model_dump(mode="json")
        task_payload["task_id"] = task.task_id
        task_payload["plan_id"] = "cached-template"
        content_basis = {
            "cache_key": cache_key,
            "statement": " ".join(task.statement.split()).casefold(),
            "answers": sorted(" ".join(answer.text.split()).casefold() for answer in task.answers),
        }
        content_hash = hashlib.sha256(
            json.dumps(content_basis, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        result = self.tasks.update_one(
            {"_id": content_hash},
            {
                "$setOnInsert": {
                    "cache_key": cache_key,
                    "content_hash": content_hash,
                    "skill_id": task.skill_id,
                    "skill_ids": task.skill_ids,
                    "subskill_ids": task.subskill_ids,
                    "task_type": task.task_type,
                    "grade": task.curriculum_context.grade,
                    "curriculum_level": task.curriculum_context.curriculum_level,
                    "difficulty": task.difficulty,
                    "metadata": metadata,
                    "task": task_payload,
                    "quality_status": "validated",
                    "created_at": now,
                    "use_count": 0,
                }
            },
            upsert=True,
        )
        if result.upserted_id is None:
            return None
        stored = self.tasks.find_one({"_id": content_hash}, {"task": 1})
        return TaskTemplate.model_validate(stored["task"])

    def save(
        self,
        presentation_id: str,
        correct_option_id: str,
        option_source_ids: dict[str, str],
        *,
        task_id: str | None = None,
        presented_task: dict | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.presentations.replace_one(
            {"_id": presentation_id},
            {
                "_id": presentation_id,
                "correct_option_id": correct_option_id,
                "option_source_ids": dict(option_source_ids),
                "task_id": task_id,
                "presented_task": presented_task,
                "created_at": now,
                "expires_at": now + timedelta(days=365),
            },
            upsert=True,
        )

    def get(self, presentation_id: str) -> dict | None:
        document = self.presentations.find_one({"_id": presentation_id})
        if document is None:
            return None
        return {
            "correct_option_id": document["correct_option_id"],
            "option_source_ids": document["option_source_ids"],
            "task_id": document.get("task_id"),
            "presented_task": document.get("presented_task"),
        }

    def close(self) -> None:
        self.client.close()
