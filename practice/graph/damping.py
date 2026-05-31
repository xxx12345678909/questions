"""
Knowledge graph prerequisite damping — pure computation.
No Flask or database dependencies.
"""
import math


def calc_prerequisite_damping(prereq_retentions, retention_threshold=0.6):
    """
    Compute prerequisite knowledge dependency damping coefficient (pure function).

    Receives a list of prerequisite node retention rates and uses a Sigmoid variant:
        sigma(x) = 1 / (1 + exp(-k(x - theta)))
        omega_q = prod(sigma(R_p))

    Args:
        prereq_retentions: list of average retention rates for prerequisites
        retention_threshold: threshold theta, default 0.6

    Returns:
        Damping coefficient omega_q in (0.0, 1.0]. Returns 1.0 if no prerequisites.
    """
    if not prereq_retentions:
        return 1.0

    k = 10.0
    damping = 1.0
    for avg_retention in prereq_retentions:
        sigma = 1.0 / (1.0 + math.exp(-k * (avg_retention - retention_threshold)))
        damping *= sigma

    return damping
