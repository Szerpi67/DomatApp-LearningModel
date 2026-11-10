
import math

import numpy as np
import pytest

from profiler.core.bayesian_updater import update


def test_correct_answer_increases_ability():
    ability, uncertainty = update(
        ability=0.0,
        uncertainty=1.0,
        difficulty=5,
        correct=True,
    )

    assert ability > 0.0
    assert math.isfinite(ability)
    assert math.isfinite(uncertainty)
    assert uncertainty > 0.0


def test_incorrect_answer_decreases_ability():
    ability, uncertainty = update(
        ability=0.0,
        uncertainty=1.0,
        difficulty=5,
        correct=False,
    )

    assert ability < 0.0
    assert math.isfinite(ability)
    assert math.isfinite(uncertainty)
    assert uncertainty > 0.0


@pytest.mark.parametrize("difficulty", [1, 10])
def test_boundary_difficulties_produce_valid_results(difficulty):
    ability, uncertainty = update(
        ability=0.0,
        uncertainty=1.0,
        difficulty=difficulty,
        correct=True,
    )

    assert math.isfinite(ability)
    assert math.isfinite(uncertainty)
    assert uncertainty > 0.0


@pytest.mark.parametrize(
    ("ability", "uncertainty", "difficulty", "correct"),
    [
        (float("nan"), 1.0, 5, True),
        (0.0, 0.0, 5, True),
        (0.0, -1.0, 5, True),
        (0.0, float("inf"), 5, True),
        (0.0, 1.0, 5, 1),
        (0.0, 1.0, 0, True),
        (0.0, 1.0, 11, True),
    ],
)
def test_invalid_inputs_raise_value_error(
    ability,
    uncertainty,
    difficulty,
    correct,
):
    with pytest.raises(ValueError):
        update(ability, uncertainty, difficulty, correct)


def test_repeated_responses_remain_numerically_valid():
    ability = 0.0
    uncertainty = 1.0

    responses = [
        True, False, True, True, False,
        True, False, True, True, False,
    ] * 5

    for correct in responses:
        ability, uncertainty = update(
            ability=ability,
            uncertainty=uncertainty,
            difficulty=5,
            correct=correct,
        )

        assert math.isfinite(ability)
        assert math.isfinite(uncertainty)
        assert uncertainty > 0.0
