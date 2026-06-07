"""
Forgetting-curve engine and recommendation scheduler — backward compatibility facade.

All functions preserve their original signatures for drop-in compatibility,
delegating internally to the new layered architecture:
  core/         — pure memory mechanism computation
  graph/        — pure graph theory and topology
  adaptive/     — pure fatigue and IRT modeling
  repository/   — SQL data extraction (I/O decoupling)
  scheduler/    — recommendation orchestration pipeline
"""
from datetime import datetime, UTC

# Re-export all pure functions from their canonical modules
from practice.core.ebbinghaus import (
    calc_retention, calc_retention_pure, calc_priority, calc_score,
    update_lambda, update_lambda_with_time_cost, evolve_lambda_by_time_cost,
    update_cost, update_accuracy, calc_time_cost_adjustment,
)
from practice.graph.damping import calc_prerequisite_damping
from practice.adaptive.fatigue import calc_fatigue, calc_fatigue_adjusted_score
from practice.adaptive.irt import calc_difficulty_damping, calc_mastery
# IRT 3PL functions: canonical home is core.irt, re-exported by adaptive.irt per 开发文档5 §3.2
from practice.core.irt import calc_irt_probability, calibrate_irt_parameters, calc_irt_difficulty_damping  # noqa: F401


# ================================================================
# Re-export repository functions used by worker threads
from practice.repository.knowledge_repo import (  # noqa: E402, F401
    update_node_mastery, batch_update_node_masteries,
)

# ================================================================
# Backward-compatible DB wrappers (delegate to repository + pure)
# ================================================================

def calc_knowledge_node_avg_retention(db, node_id, now=None):
    """Deprecated: use repository.get_node_avg_retention() directly."""
    from practice.repository.knowledge_repo import get_node_avg_retention
    return get_node_avg_retention(db, node_id, now)


def calc_prerequisite_damping_db(db, question_id, now=None, retention_threshold=0.6):
    """Fetch prereq retentions from DB, then call pure calc_prerequisite_damping."""
    from practice.repository.knowledge_repo import get_prerequisite_retentions
    prereq_retentions = get_prerequisite_retentions(db, question_id, now)
    return calc_prerequisite_damping(prereq_retentions, retention_threshold)


def calc_sliding_window_accuracy(db, node_id, window_size=5):
    """Fetch sliding window accuracy from DB."""
    from practice.repository.knowledge_repo import get_node_sliding_accuracy
    return get_node_sliding_accuracy(db, node_id, window_size)


def calc_mastery_db(db, node_id, now=None):
    """Fetch node stats from DB, then call pure calc_mastery."""
    from practice.repository.knowledge_repo import (
        get_node_avg_retention, get_node_sliding_accuracy, get_node_avg_lambda
    )
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)
    avg_retention = get_node_avg_retention(db, node_id, now)
    rolling_acc = get_node_sliding_accuracy(db, node_id)
    avg_lambda = get_node_avg_lambda(db, node_id)
    return calc_mastery(avg_retention, rolling_acc, avg_lambda)


def recommend_learning_path(db, target_node_id, mastery_threshold=0.7):
    """
    DAG-based shortest learning path recommendation.

    Uses repository for data extraction and graph/pathfinder for pure topology.
    """
    from practice.repository.knowledge_repo import (
        fetch_target_node, fetch_reversed_dependency_tree,
        fetch_all_nodes_mastery_and_names
    )
    from practice.graph.pathfinder import build_topo_learning_path

    target = fetch_target_node(db, target_node_id)
    if not target:
        return {'error': '目标知识点不存在'}

    adjacency_tree = fetch_reversed_dependency_tree(db, target_node_id, mastery_threshold)

    all_nodes = {target_node_id}
    for node_id, prereqs in adjacency_tree.items():
        all_nodes.add(node_id)
        all_nodes.update(prereqs)

    node_masteries, node_names = fetch_all_nodes_mastery_and_names(db, all_nodes)

    return build_topo_learning_path(
        target_node_id, target['name'],
        adjacency_tree, node_masteries, node_names, mastery_threshold
    )


def recommend_questions_advanced(db, budget, review_ratio, wrong_ratio, new_ratio,
                                  threshold, max_consecutive, shuffle_within=False,
                                  enable_knowledge_graph=True,
                                  fatigue=None, enable_difficulty_adaptation=False,
                                  enable_irt=False):
    """
    Generate recommended question list with multi-dimensional damping.

    Delegates to scheduler/orchestrator.
    """
    from practice.scheduler.orchestrator import build_recommendation
    return build_recommendation(
        db, budget, review_ratio, wrong_ratio, new_ratio,
        threshold, max_consecutive, shuffle_within=shuffle_within,
        enable_knowledge_graph=enable_knowledge_graph,
        fatigue=fatigue,
        enable_difficulty_adaptation=enable_difficulty_adaptation,
        enable_irt=enable_irt,
    )


def recommend_questions(db, budget, review_ratio, wrong_ratio, new_ratio,
                        threshold, max_consecutive, shuffle_within=False):
    """
    Backward-compatible recommendation without knowledge graph.
    Calls advanced version with knowledge graph disabled.
    """
    return recommend_questions_advanced(
        db, budget, review_ratio, wrong_ratio, new_ratio,
        threshold, max_consecutive, shuffle_within=shuffle_within,
        enable_knowledge_graph=False
    )
