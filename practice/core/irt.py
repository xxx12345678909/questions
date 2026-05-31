"""
IRT (Item Response Theory) 3PL model — pure computation.
Online SGD-based parameter calibration replacing heuristic Gaussian difficulty matching.

All functions are pure: no Flask or database dependencies.
"""
import math


# Logistic => Normal scaling constant for 3PL model
D = 1.702


def calc_irt_probability(theta, irt_a, irt_b, irt_c):
    """
    Compute the theoretical probability P_i(theta) of a correct answer
    under the 3PL (Three-Parameter Logistic) model.

    P_i(theta) = c_i + (1 - c_i) / (1 + exp(-D * a_i * (theta - b_i)))

    Args:
        theta: user's latent ability on this knowledge node, range [-3, 3]
        irt_a:  item discrimination parameter, range [0.5, 2.5]
        irt_b:  item difficulty parameter, range [-3, 3]
        irt_c:  guessing (pseudo-chance) parameter, typically 0.0 or 0.25

    Returns:
        Probability of correct response, clamped to [0.001, 0.999]
    """
    try:
        exponent = -D * irt_a * (theta - irt_b)
        if exponent > 50:
            return irt_c
        if exponent < -50:
            return 1.0

        logistic_part = 1.0 / (1.0 + math.exp(exponent))
        prob = irt_c + (1.0 - irt_c) * logistic_part
        return max(0.001, min(0.999, prob))
    except OverflowError:
        return irt_c


def calibrate_irt_parameters(theta, irt_a, irt_b, irt_c, is_correct,
                              lr_theta=0.4, lr_item=0.1):
    """
    Online SGD-based streaming parameter calibration.

    Updates user ability (theta), item difficulty (b), and item discrimination (a)
    based on the residual between actual outcome U and predicted probability P.

    Args:
        theta:  current user ability estimate
        irt_a:  current item discrimination
        irt_b:  current item difficulty
        irt_c:  current item guessing parameter
        is_correct: actual outcome (True/False)
        lr_theta: learning rate for ability update (default 0.4)
        lr_item:  learning rate for item parameter update (default 0.1)

    Returns:
        (new_theta, new_a, new_b) tuple, each clamped to valid range
    """
    u = 1.0 if is_correct else 0.0
    p = calc_irt_probability(theta, irt_a, irt_b, irt_c)
    residual = u - p

    # Standard derivative multiplier for 3PL gradient ascent
    multiplier = D * (p - irt_c) / (1.0 - irt_c) * (1.0 - p)

    # 1. Update user latent ability theta
    delta_theta = lr_theta * irt_a * residual
    new_theta = max(-3.0, min(3.0, theta + delta_theta))

    # 2. Update item difficulty b
    delta_b = -lr_item * irt_a * multiplier * residual
    new_b = max(-3.0, min(3.0, irt_b + delta_b))

    # 3. Update item discrimination a
    delta_a = lr_item * (theta - irt_b) * multiplier * residual
    new_a = max(0.2, min(3.5, irt_a + delta_a))

    return round(new_theta, 4), round(new_a, 4), round(new_b, 4)


def calc_irt_difficulty_damping(theta, irt_b, max_damping_span=1.5):
    """
    IRT-based ZPD (Zone of Proximal Development) recommendation damping factor.

    Replaces the old Gaussian kernel matching. Peak damping (~1.0) occurs when
    item difficulty is slightly above user ability (sweet spot: b - theta ~= 0.3).

    Args:
        theta:  user ability
        irt_b:  item difficulty
        max_damping_span: controls width of sweet-spot zone

    Returns:
        Damping coefficient in (0, 1]
    """
    deviation = irt_b - theta
    sweet_spot_deviation = deviation - 0.3
    damping = math.exp(-(sweet_spot_deviation ** 2) / (2 * (0.6 ** 2)))
    return round(damping, 4)
