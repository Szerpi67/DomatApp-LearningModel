from __future__ import annotations

from hashlib import sha256
import json

from planner.models.learning_plan import TaskAllocation
from task_generator.models import TaskGenerationRequest
from task_generator.service import GENERATOR_VERSION, TASK_PROMPT_VERSION, TaskGenerationError, TaskGeneratorService


class CachedTaskService:
    """Serve matching tasks from MongoDB and persist Gemini cache misses."""

    def __init__(self, generator: TaskGeneratorService, store):
        self.generator = generator
        self.store = store

    def stream_tasks(self, request: TaskGenerationRequest, *, student_id: str | None = None):
        skill = self.generator.skills.get(request.plan_item.skill_id)
        if skill is None:
            raise TaskGenerationError(
                "missing_skill_definition",
                f"No curriculum definition is available for '{request.plan_item.skill_id}'.",
            )
        self.generator._validate_context(
            request.plan_item.skill_id, request.curriculum_context
        )
        learner_key = (
            sha256(student_id.strip().casefold().encode("utf-8")).hexdigest()
            if student_id and student_id.strip()
            else None
        )
        for allocation in request.plan_item.task_allocations:
            cache_key, metadata = self._cache_key(request, skill, allocation.task_type)
            cached = self.store.take_cached_tasks(
                cache_key,
                allocation.count,
                desired_difficulty=round(request.plan_item.target_difficulty),
                learner_key=learner_key,
            )
            seen_content = set()
            unique_cached = []
            for task in cached:
                fingerprint = self._task_fingerprint(task)
                if fingerprint in seen_content:
                    continue
                seen_content.add(fingerprint)
                unique_cached.append(task)
            for task in unique_cached:
                yield task.model_copy(
                    update={"plan_id": request.plan_id}
                )

            missing = allocation.count - len(unique_cached)
            empty_batches = 0
            while missing > 0:
                chunk_size = min(missing, 10)
                chunk_request = request.model_copy(
                    update={
                        "plan_item": request.plan_item.model_copy(
                            update={
                                "task_allocations": [
                                    TaskAllocation(
                                        task_type=allocation.task_type,
                                        count=chunk_size,
                                    )
                                ]
                            }
                        )
                    }
                )
                generated_count = 0
                try:
                    for generated_task in self.generator.generate_batch_stream(chunk_request):
                        fingerprint = self._task_fingerprint(generated_task)
                        if fingerprint in seen_content:
                            continue
                        task = self.store.save_task(cache_key, generated_task, metadata)
                        if task is None:
                            seen_content.add(fingerprint)
                            continue
                        seen_content.add(fingerprint)
                        task = task.model_copy(update={"plan_id": request.plan_id})
                        if learner_key:
                            self.store.mark_task_seen(learner_key, cache_key, task.task_id)
                        generated_count += 1
                        missing -= 1
                        yield task
                except TaskGenerationError as error:
                    if error.code != "gemini_format_failed":
                        raise
                    empty_batches = 0 if generated_count else empty_batches + 1
                    if empty_batches >= 3:
                        raise
                    continue
                if generated_count == 0:
                    empty_batches += 1
                else:
                    empty_batches = 0
                if empty_batches >= 3:
                    raise TaskGenerationError(
                        "task_generation_stalled",
                        "Gemini did not produce enough distinct, valid tasks after three batches.",
                    )

    def prefill_pool(self, request: TaskGenerationRequest, pool_size: int):
        """Generate and persist tasks until this exact skill/context cache has a pool."""
        if pool_size < 1 or pool_size > 100:
            raise TaskGenerationError(
                "invalid_pool_size", "Pool size must be between 1 and 100."
            )
        if len(request.plan_item.task_allocations) != 1:
            raise TaskGenerationError(
                "invalid_pool_request", "Prefill currently accepts one task type per request."
            )
        allocation = request.plan_item.task_allocations[0]
        if allocation.task_type != "closed_single_choice":
            raise TaskGenerationError(
                "unsupported_task_type",
                f"Task type '{allocation.task_type}' is not supported yet.",
            )
        skill = self.generator.skills.get(request.plan_item.skill_id)
        if skill is None:
            raise TaskGenerationError(
                "missing_skill_definition",
                f"No curriculum definition is available for '{request.plan_item.skill_id}'.",
            )
        cache_key, metadata = self._cache_key(request, skill, allocation.task_type)
        desired_difficulty = round(request.plan_item.target_difficulty)
        initial_count = self.store.count_cached_tasks(
            cache_key, difficulty=desired_difficulty
        )
        while self.store.count_cached_tasks(
            cache_key, difficulty=desired_difficulty
        ) < pool_size:
            before_count = self.store.count_cached_tasks(
                cache_key, difficulty=desired_difficulty
            )
            missing = pool_size - before_count
            chunk_size = min(missing, 10)
            chunk_request = request.model_copy(
                update={
                    "plan_item": request.plan_item.model_copy(
                        update={
                            "task_allocations": [
                                TaskAllocation(
                                    task_type=allocation.task_type,
                                    count=chunk_size,
                                )
                            ]
                        }
                    )
                }
            )
            for task in self.generator.generate_batch_stream(chunk_request):
                self.store.save_task(cache_key, task, metadata)
                available = self.store.count_cached_tasks(
                    cache_key, difficulty=desired_difficulty
                )
                yield {"available": available, "target": pool_size}
            after_count = self.store.count_cached_tasks(
                cache_key, difficulty=desired_difficulty
            )
            if after_count <= before_count:
                raise TaskGenerationError(
                    "cache_prefill_stalled",
                    "Gemini produced no new distinct tasks for this pool. Retry with a different skill or context.",
                )
        yield {
            "available": self.store.count_cached_tasks(
                cache_key, difficulty=desired_difficulty
            ),
            "target": pool_size,
            "initial": initial_count,
        }

    def _cache_key(self, request, skill, task_type):
        skill_json = json.dumps(skill, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        skill_digest = sha256(skill_json.encode("utf-8")).hexdigest()
        item = request.plan_item
        metadata = {
            "skill_id": item.skill_id,
            "skill_ids": item.skill_ids,
            "subskill_ids": item.subskill_ids,
            "subskills_by_skill": item.subskills_by_skill,
            "skill_digest": skill_digest,
            "skill_digests": {
                skill_id: sha256(
                    json.dumps(
                        self.generator.skills[skill_id],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                for skill_id in item.skill_ids
            },
            "task_type": task_type,
            "grade": request.curriculum_context.grade,
            "education_stage": request.curriculum_context.education_stage,
            "curriculum_level": request.curriculum_context.curriculum_level,
            "curriculum_version": request.curriculum_context.curriculum_version,
            "material_scope": item.material_scope,
            "model": self.generator.gemini_model,
            "generator_version": GENERATOR_VERSION,
            "prompt_version": TASK_PROMPT_VERSION,
        }
        if item.skill_id == "MAT-FRA-005":
            metadata["semantic_validation_version"] = "single-true-comparison-v1"
        encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest(), metadata

    @staticmethod
    def _task_fingerprint(task):
        basis = {
            "statement": " ".join(task.statement.split()).casefold(),
            "answers": sorted(" ".join(answer.text.split()).casefold() for answer in task.answers),
        }
        return sha256(
            json.dumps(basis, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
