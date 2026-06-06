"""
IRT/IRM-inspired adaptive difficulty and mastery modeling — pure computation.
No Flask or database dependencies.

The 3PL IRT core (calc_irt_probability, calibrate_irt_parameters,
calc_irt_difficulty_damping) lives in practice.core.irt and is re-exported
here per the module spec in 开发文档5 §3.2.
"""
import math

# Re-export 3PL IRT functions from their canonical pure-math location
from practice.core.irt import (           # noqa: E402, F401
    calc_irt_probability,
    calibrate_irt_parameters,
    calc_irt_difficulty_damping,
)


def calc_difficulty_damping(diff_q, theta_n, sigma=0.20):
    """
    Compute difficulty-match damping Omega_diff using Gaussian kernel.

    Omega_diff = exp( -(diff_q - theta_n)^2 / (2 * sigma^2) )

    If user's current win rate theta_n = 0.8, questions with difficulty near
    0.75-0.85 receive highest gain (~1.0); questions that are too easy or
    too hard receive suppression.

    Args:
        diff_q: static difficulty coefficient of the question
        theta_n: user's sliding-window accuracy on this knowledge node
        sigma: difficulty matching bandwidth, default 0.20

    Returns:
        Damping coefficient Omega_diff in (0, 1]
    """
    deviation = diff_q - theta_n
    return math.exp(-(deviation ** 2) / (2 * sigma ** 2))


def calc_mastery(avg_retention, rolling_acc, avg_lambda):
    """
    Compute composite knowledge node mastery M_n (pure function).

    M_n = 0.4 * avg_retention + 0.35 * rolling_acc + 0.25 * (1.0 - avg_lambda_norm)

    Args:
        avg_retention: average retention across node questions (0.0-1.0)
        rolling_acc: sliding window accuracy theta_n (0.0-1.0)
        avg_lambda: average forgetting rate (raw, not normalized)

    Returns:
        Mastery M_n in [0, 1]
    """
    avg_lambda_norm = min(1.0, avg_lambda / 5.0)
    M_n = 0.4 * avg_retention + 0.35 * rolling_acc + 0.25 * (1.0 - avg_lambda_norm)
    return max(0.0, min(1.0, M_n))
