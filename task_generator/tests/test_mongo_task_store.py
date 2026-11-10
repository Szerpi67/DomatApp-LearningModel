from datetime import timedelta
from unittest.mock import Mock

from task_generator.repositories.mongo_task_store import (
    LEARNING_PLAN_RETENTION_DAYS,
    MongoTaskStore,
)


def test_save_learning_plan_sets_90_day_expiration():
    store = object.__new__(MongoTaskStore)
    store.learning_plans = Mock()
    plan = Mock(plan_id="plan-1")
    plan.model_dump.return_value = {"plan_id": "plan-1"}
    context = Mock()
    context.model_dump.return_value = {"grade": 8}

    store.save_learning_plan(plan, "student-1", context)

    document = store.learning_plans.replace_one.call_args.args[1]
    assert document["expires_at"] - document["created_at"] == timedelta(
        days=LEARNING_PLAN_RETENTION_DAYS
    )


def test_get_learning_plan_refreshes_expiration_and_returns_plan():
    store = object.__new__(MongoTaskStore)
    store.learning_plans = Mock()
    store.learning_plans.find_one.return_value = {
        "plan": {"plan_id": "plan-1"},
        "user_id": "student-1",
        "curriculum_context": {"grade": 8},
        "task_assignments": [],
    }

    saved = store.get_learning_plan("plan-1")

    assert saved["plan"]["plan_id"] == "plan-1"
    update = store.learning_plans.update_one.call_args.args[1]["$set"]
    assert update["expires_at"] - update["updated_at"] == timedelta(
        days=LEARNING_PLAN_RETENTION_DAYS
    )
    assert store.learning_plans.find_one.call_args.args[0]["_id"] == "plan-1"


def test_add_plan_task_refreshes_expiration_only_for_active_plans():
    store = object.__new__(MongoTaskStore)
    store.learning_plans = Mock()

    store.add_plan_task("plan-1", 2, "task-1")

    query, update = store.learning_plans.update_one.call_args.args
    expiration = update["$set"]["expires_at"]
    assert expiration - update["$set"]["updated_at"] == timedelta(
        days=LEARNING_PLAN_RETENTION_DAYS
    )
    assert query["_id"] == "plan-1"
    assert query["$or"][0]["expires_at"]["$gt"] < expiration
