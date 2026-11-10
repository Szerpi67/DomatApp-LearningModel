
import numpy as np

from profiler.core.irt_model import (
    GH_NODES,
    GH_WEIGHTS,
    difficulty_to_b,
)


def update(
        ability: float,
    uncertainty: float,
    difficulty: int,
    correct: bool,
) -> tuple[float, float]:


    if not np.isfinite(ability):
        raise ValueError("Ability must be finite")

    if not np.isfinite(uncertainty) or uncertainty <= 0:
        raise ValueError("Uncertainty must be positive and finite")

    if not isinstance(correct, (bool, np.bool_)):
        raise ValueError("Correct must be a boolean")


    b = difficulty_to_b(difficulty)


    theta = (
        ability
        + np.sqrt(2.0) * uncertainty * GH_NODES
    )


    logits = theta - b


    probabilities = np.exp(
        -np.logaddexp(0.0, -logits)
    )


    if correct:
        likelihood = probabilities
    else:
        likelihood = 1.0 - probabilities


    posterior_weights = GH_WEIGHTS * likelihood


    total_weight = np.sum(posterior_weights)

    if not np.isfinite(total_weight) or total_weight <= 0:
        raise ValueError("Posterior normalization failed")

    posterior_weights /= total_weight


    new_ability = float(
        np.dot(posterior_weights, theta)
    )


    new_variance = float(
        np.dot(
            posterior_weights,
            (theta - new_ability) ** 2,
        )
    )


    new_uncertainty = float(
        np.sqrt(max(new_variance, 1e-12))
    )

    return new_ability, new_uncertainty

