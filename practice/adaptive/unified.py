"""
Unified Probabilistic Scoring Model — collapses the 4-dimensional damping
cascade into a single differentiable scoring function.

    Score_i = (1 - R_i(t)) * w_subj * Ω_graph * Ω_irt / max(cost, 1) * (1 - F * diff_i)

    where:
      R_i(t)  = exp(-lambda_i * hours_since_review)     — Ebbinghaus retention
      Ω_graph = Π σ(R_p)                                 — prerequisite damping
      Ω_irt   = exp(-((b - θ) - 0.3)² / (2 * 0.6²))    — IRT ZPD damping
      F       = fatigue factor                           — session fatigue

All terms are computed in a single O(k) pass (k = prerequisite count).
No Flask or database dependencies — pure computation.
"""
import math


def unified_score(theta_irt, lambda_, seconds_since_review, subject_weight,
                   irt_b, prereq_retentions, avg_cost, times_wrong,
                   fatigue=0.0, difficulty=0.5, retention_threshold=0.6):
    """
    Compute the unified scheduling score for one question.

    Args:
        theta_irt:        user IRT ability on this knowledge node (default 0.0)
        lambda_:          forgetting rate λ ∈ [0.01, 5.0]
        seconds_since_review: seconds since last review (None → never reviewed)
        subject_weight:   subject priority weight (e.g. 1.2 for 408)
        irt_b:            item difficulty parameter b ∈ [-3, 3]
        prereq_retentions: list of prerequisite node retention rates [0..1]
        avg_cost:         average time cost (minutes)
        times_wrong:      number of wrong answers to this question
        fatigue:          session fatigue factor F ∈ [0, 1)
        difficulty:       static difficulty coefficient of the question
        retention_threshold: threshold for prerequisite retention (default 0.6)

    Returns:
        (score, priority, retention, graph_damping, irt_damping, fatigue_factor)
    """
    # ---- 1. Ebbinghaus retention ----
    if seconds_since_review is None or seconds_since_review < 0:
        retention = 0.0
    else:
        delta_hours = seconds_since_review / 3600.0
        retention = math.exp(-lambda_ * delta_hours)

    # ---- 2. Base priority = urgency × weight ----
    priority = (1.0 - retention) * subject_weight
    if times_wrong > 0:
        priority += 0.5  # wrong-answer bonus

    # ---- 3. Score = priority / cost ----
    effective_cost = max(avg_cost, 1.0)
    score = priority / effective_cost

    # ---- 4. Knowledge graph prerequisite damping ----
    graph_damping = 1.0
    if prereq_retentions:
        k = 10.0
        graph_damping = 1.0
        for r_p in prereq_retentions:
            sigma = 1.0 / (1.0 + math.exp(-k * (r_p - retention_threshold)))
            graph_damping *= sigma
    score *= graph_damping

    # ---- 5. IRT ZPD (Zone of Proximal Development) damping ----
    irt_damping = 1.0
    if theta_irt is not None and irt_b is not None:
        deviation = irt_b - theta_irt
        sweet_spot_deviation = deviation - 0.3
        irt_damping = math.exp(-(sweet_spot_deviation ** 2) / (2 * 0.6 ** 2))
    score *= irt_damping

    # ---- 6. Fatigue down-weighting ----
    fatigue_factor = 1.0
    if fatigue > 0:
        fatigue_factor = 1.0 - fatigue * difficulty
        score *= fatigue_factor

    return (round(score, 4), round(priority, 4), round(retention, 4),
            round(graph_damping, 4), round(irt_damping, 4),
            round(fatigue_factor, 4))
