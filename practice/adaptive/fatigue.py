"""
Session fatigue modeling — pure computation.
No Flask or database dependencies.
"""
import math


def calc_fatigue(session_duration_minutes, question_count, alpha=0.015, beta=0.025):
    """
    Calculate current session fatigue factor F.

    F = 1.0 - exp(-(alpha * T_session + beta * N_session))

    Args:
        session_duration_minutes: continuous session duration in minutes
        question_count: number of questions answered in session
        alpha: time sensitivity coefficient, default 0.015
        beta: question-count sensitivity coefficient, default 0.025

    Returns:
        Fatigue factor F in [0, 1)
    """
    raw = alpha * session_duration_minutes + beta * question_count
    return 1.0 - math.exp(-raw)


def calc_fatigue_adjusted_score(base_score, fatigue, difficulty):
    """
    Apply fatigue-based weight suppression to scheduling score.

    Score_final = Score_base * (1.0 - F * Difficulty_q)
    High-difficulty questions receive stronger suppression during fatigue.

    Args:
        base_score: base scheduling score
        fatigue: fatigue factor F
        difficulty: question difficulty coefficient (0.0-1.0)

    Returns:
        Fatigue-adjusted final score
    """
    return base_score * (1.0 - fatigue * difficulty)
