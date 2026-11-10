
import numpy as np

from profiler.core.bayesian_updater import update
from profiler.core.irt_model import difficulty_to_b


def sigmoid(x: float) -> float:
    return float(np.exp(-np.logaddexp(0.0, -x)))


def run_simulation(
    n_students: int = 4000,
    n_tasks: int = 100,
    seed: int = 42,
) -> None:
    rng = np.random.default_rng(seed)

    # Ukryte, rzeczywiste umiejętności uczniów.
    # Rozkład odpowiada początkowemu priorowi N(0, 1).
    true_abilities = rng.normal(
        loc=0.0,
        scale=1.0,
        size=n_students,
    )

    # Każdy uczeń dostaje zadania o różnych trudnościach.
    difficulties = rng.integers(
        low=1,
        high=11,
        size=(n_students, n_tasks),
    )

    # Odpowiedzi generujemy według modelu Rascha.
    responses = np.zeros(
        (n_students, n_tasks),
        dtype=bool,
    )
    # Losowy błąd: odpowiedź zostaje odwrócona
    # z prawdopodobieństwem 10%.

    for i in range(n_students):
        for j in range(n_tasks):
            b = difficulty_to_b(int(difficulties[i, j]))
            probability = sigmoid(true_abilities[i] - b)

            # Losowy błąd: odpowiedź zostaje odwrócona
            # z prawdopodobieństwem 10%.
            response = rng.random() < probability

            if rng.random() < 0.05:
                response = not response

            responses[i, j] = response

    # Profiler nie otrzymuje true_abilities.
    estimated_abilities = np.zeros(n_students)
    uncertainties = np.ones(n_students)

    checkpoints = {5, 10, 20, 30, 50, 60, 70, 90, 100}
    results = {}

    for task_index in range(n_tasks):
        for student_index in range(n_students):
            estimated_abilities[student_index], uncertainties[
                student_index
            ] = update(
                ability=float(
                    estimated_abilities[student_index]
                ),
                uncertainty=float(
                    uncertainties[student_index]
                ),
                difficulty=int(
                    difficulties[student_index, task_index]
                ),
                correct=bool(
                    responses[student_index, task_index]
                ),
            )

        tasks_done = task_index + 1

        if tasks_done in checkpoints:
            results[tasks_done] = (
                estimated_abilities.copy(),
                uncertainties.copy(),
            )

    # Oceniamy wyniki dopiero po zakończeniu aktualizacji.
    initial_rmse = float(
        np.sqrt(np.mean(true_abilities ** 2))
    )

    print(f"Liczba uczniów: {n_students}")
    print(f"Liczba zadań na ucznia: {n_tasks}")
    print(f"Łączna liczba odpowiedzi: {n_students * n_tasks}")
    print(f"RMSE początkowego oszacowania (theta=0): {initial_rmse:.4f}")
    print()

    print(
        f"{'Zadania':>8} "
        f"{'MAE':>10} "
        f"{'RMSE':>10} "
        f"{'Bias':>10} "
        f"{'Śr. niepewność':>16} "
        f"{'Pokrycie 90%':>14}"
    )

    for tasks_done, (estimates, stds) in results.items():
        errors = estimates - true_abilities

        mae = float(np.mean(np.abs(errors)))
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        bias = float(np.mean(errors))
        mean_uncertainty = float(np.mean(stds))

        # Przybliżony przedział 90% dla rozkładu normalnego.
        covered = np.abs(errors) <= 1.645 * stds
        coverage = float(np.mean(covered))

        print(
            f"{tasks_done:>8} "
            f"{mae:>10.4f} "
            f"{rmse:>10.4f} "
            f"{bias:>10.4f} "
            f"{mean_uncertainty:>16.4f} "
            f"{coverage:>13.1%}"
        )


if __name__ == "__main__":
    run_simulation()
