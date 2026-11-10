import numpy as np

GH_NODES, GH_WEIGHTS = np.polynomial.hermite.hermgauss(20)

def difficulty_to_b(level: int) -> float:
    if not 1 <= level <= 10:
        raise ValueError("Difficulty must be between 1 and 10")

    return -2.7 + (level - 1) * 0.6


def predict_probability(ability: float, uncertainty: float, b: float):
    """Estimate the response probability averaged over ability uncertainty.

    ``ability`` is the mean of a normal ability estimate and ``uncertainty``
    its standard deviation. Gauss-Hermite nodes integrate the logistic
    response function against that normal distribution.
    """
    if not all(np.isfinite(v) for v in (ability, uncertainty, b)):
        raise ValueError("IRT parameters must be finite")

    if uncertainty <= 0:
        raise ValueError("Uncertainty must be positive")

    ability_samples = ability + np.sqrt(2) * uncertainty * GH_NODES
    logits = np.clip(ability_samples - b, -709, 709)
    probabilities = 1 / (1 + np.exp(-logits))

    return float(np.dot(GH_WEIGHTS, probabilities) / np.sqrt(np.pi))


def calculate_difficulty_mastery(ability: float, uncertainty: float):
    difficulty_mastery: dict[int,float] = {}

    for x in range(1, 11):
        b = difficulty_to_b(x)
        probability = predict_probability(ability, uncertainty, b)
        difficulty_mastery[x] = probability
    return difficulty_mastery

