"""
Core micro-memory mechanism — pure Ebbinghaus forgetting curve functions.
No Flask or database dependencies. All functions receive data as arguments.
"""
import math
from datetime import datetime, UTC

from practice import SUBJECT_WEIGHTS


# ================================================================
# Core retention calculation
# ================================================================

def calc_retention(lambda_, last_review, now=None):
    """R(t) = exp(-lambda * hours_since_review). Returns 0.0 if never reviewed."""
    if last_review is None:
        return 0.0
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)
    if isinstance(last_review, str):
        last_review = datetime.fromisoformat(last_review)
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    delta = (now - last_review).total_seconds() / 3600.0
    if delta < 0:
        return 1.0
    return math.exp(-lambda_ * delta)


def calc_retention_pure(lambda_val, seconds_since_review):
    """
    【纯函数】R(t) = exp(-lambda * hours_since_review)
    输入 Lambda 隐变量与距离上一次复习的精确秒数，求解记忆保留率。
    与 calc_retention 不同，此函数不依赖 datetime 对象，完全基于秒数计算。
    """
    if seconds_since_review is None or seconds_since_review < 0:
        return 0.0
    delta_hours = seconds_since_review / 3600.0
    return math.exp(-lambda_val * delta_hours)


def calc_priority(lambda_, last_review, subject, times_wrong, now=None):
    """priority = (1 - retention) * weight, with subject + wrong bonus."""
    r = calc_retention(lambda_, last_review, now)
    weight = SUBJECT_WEIGHTS.get(subject, 1.0)
    if times_wrong > 0:
        weight += 0.5
    return (1.0 - r) * weight, r


def calc_score(lambda_, last_review, subject, times_wrong, avg_cost, now=None):
    """score = priority / avg_cost (ratio-based scheduling)."""
    priority, r = calc_priority(lambda_, last_review, subject, times_wrong, now)
    score = priority / max(avg_cost, 1.0)
    return score, priority, r


# ================================================================
# Time-cost deviation and Lambda evolution
# ================================================================

def calc_time_cost_adjustment(time_spent, avg_cost):
    """
    Calculate time-cost deviation coefficient.
    gamma = time_spent / avg_cost
    """
    if avg_cost <= 0:
        avg_cost = 5.0
    return time_spent / avg_cost


def update_lambda_with_time_cost(old_lambda, success, time_spent, avg_cost):
    """
    Dynamic lambda update incorporating time-cost deviation.

    Case A (correct but struggling): success=True and gamma > 1.5
        lambda_new = lambda_old * (0.8 + 0.15 * min(1.0, gamma - 1))
    Case B (wrong and fast): success=False and gamma < 0.3
        lambda_new = lambda_old * 1.3
    Default: success? lambda*0.8 : lambda*1.2

    Clamped to [0.01, 5.0].
    """
    gamma = calc_time_cost_adjustment(time_spent, avg_cost)

    if success and gamma > 1.5:
        factor = 0.8 + 0.15 * min(1.0, gamma - 1.0)
        new_lambda = old_lambda * factor
    elif not success and gamma < 0.3:
        new_lambda = old_lambda * 1.3
    else:
        new_lambda = old_lambda * 0.8 if success else old_lambda * 1.2

    return max(0.01, min(5.0, new_lambda))


# Documented alias — matches the spec name in 开发文档5 §3.1
evolve_lambda_by_time_cost = update_lambda_with_time_cost


def update_lambda(old_lambda, success):
    """
    Simple lambda update. Success: lambda*0.8. Failure: lambda*1.2.
    (deprecated — use update_lambda_with_time_cost instead)
    """
    new_lambda = old_lambda * 0.8 if success else old_lambda * 1.2
    return max(0.01, min(5.0, new_lambda))


def update_cost(old_cost, new_cost):
    """EMA: 0.7 * old + 0.3 * new, rounded to 1 decimal."""
    return round(0.7 * old_cost + 0.3 * new_cost, 1)


def update_accuracy(times_correct, times_wrong, success):
    """Rolling accuracy = times_correct / total."""
    if success:
        times_correct += 1
    else:
        times_wrong += 1
    total = times_correct + times_wrong
    acc = times_correct / total if total > 0 else 0.0
    return acc, times_correct, times_wrong
