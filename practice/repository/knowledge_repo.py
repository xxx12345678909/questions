"""
Knowledge repository — SQL extraction layer for knowledge nodes and dependencies.
Receives db connection as first parameter (Flask g-based connection model).
"""
import math
from datetime import datetime, UTC

from practice import DEFAULT_CONFIG


# ================================================================
# Internal helpers
# ================================================================

def _calc_retention_local(lambda_, last_review, now):
    """Inline retention calc to avoid circular import from core.ebbinghaus."""
    if last_review is None:
        return 0.0
    if isinstance(last_review, str):
        last_review = datetime.fromisoformat(last_review)
    delta = (now - last_review).total_seconds() / 3600.0
    if delta < 0:
        return 1.0
    return math.exp(-lambda_ * delta)


# ================================================================
# Prerequisite retention queries
# ================================================================

def get_prerequisite_retentions(db, question_id, now=None):
    """Fetch retention values for all prerequisite knowledge nodes of a question."""
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)

    node_rows = db.execute(
        'SELECT DISTINCT node_id FROM question_node_mapping WHERE question_id = ?',
        (question_id,)
    ).fetchall()
    if not node_rows:
        return []

    prereq_retentions = []
    for nr in node_rows:
        dep_rows = db.execute(
            'SELECT prerequisite_node_id FROM knowledge_dependency WHERE node_id = ?',
            (nr['node_id'],)
        ).fetchall()
        for dr in dep_rows:
            prereq_id = dr['prerequisite_node_id']
            rows = db.execute('''
                SELECT s.lambda_, s.last_review
                FROM user_question_state s
                JOIN question_node_mapping m ON s.question_id = m.question_id
                WHERE m.node_id = ?
            ''', (prereq_id,)).fetchall()
            if rows:
                avg_r = sum(_calc_retention_local(r['lambda_'], r['last_review'], now) for r in rows) / len(rows)
            else:
                avg_r = 0.8
            prereq_retentions.append(avg_r)
    return prereq_retentions


# ================================================================
# Knowledge node aggregate queries
# ================================================================

def get_node_avg_retention(db, node_id, now=None):
    """Get average retention across all questions in a knowledge node."""
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)
    rows = db.execute('''
        SELECT s.lambda_, s.last_review
        FROM user_question_state s
        JOIN question_node_mapping m ON s.question_id = m.question_id
        WHERE m.node_id = ?
    ''', (node_id,)).fetchall()
    if not rows:
        return 0.8
    return sum(_calc_retention_local(r['lambda_'], r['last_review'], now) for r in rows) / len(rows)


def get_node_sliding_accuracy(db, node_id, window_size=5):
    """Get sliding window accuracy for a knowledge node."""
    rows = db.execute('''
        SELECT ar.is_correct
        FROM answer_records ar
        JOIN question_node_mapping qnm ON ar.question_id = qnm.question_id
        WHERE qnm.node_id = ?
        ORDER BY ar.created_at DESC
        LIMIT ?
    ''', (node_id, window_size)).fetchall()
    if not rows:
        return 0.5
    return sum(1 for r in rows if r['is_correct']) / len(rows)


def get_node_avg_lambda(db, node_id):
    """Get average lambda (forgetting rate) for a knowledge node."""
    row = db.execute('''
        SELECT AVG(s.lambda_) as avg_lambda
        FROM user_question_state s
        JOIN question_node_mapping m ON s.question_id = m.question_id
        WHERE m.node_id = ?
    ''', (node_id,)).fetchone()
    return (row['avg_lambda'] or 0.3) if row else 0.3


def update_node_mastery(db, node_id):
    """Recalculate and persist knowledge node mastery + rolling accuracy.

    Directly imports calc_mastery pure function from adaptive.irt to avoid
    circular dependency with engine.py.
    """
    from practice.adaptive.irt import calc_mastery

    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        avg_retention = get_node_avg_retention(db, node_id, now)
        rolling_acc = get_node_sliding_accuracy(db, node_id)
        avg_lambda = get_node_avg_lambda(db, node_id)
        mastery = calc_mastery(avg_retention, rolling_acc, avg_lambda)
        db.execute(
            'UPDATE knowledge_nodes SET current_mastery = ?, rolling_accuracy = ? WHERE id = ?',
            (round(mastery, 4), round(rolling_acc, 4), node_id)
        )
        db.commit()
    except Exception:
        pass


# ================================================================
# Dependency tree extraction (for pathfinder)
# ================================================================

def fetch_target_node(db, node_id):
    """Fetch basic node info by ID."""
    return db.execute(
        'SELECT id, name, subject FROM knowledge_nodes WHERE id = ?',
        (node_id,)
    ).fetchone()


def fetch_reversed_dependency_tree(db, target_node_id, mastery_threshold=0.7):
    """
    BFS reverse traversal: collect all prerequisite nodes below mastery threshold.

    Returns:
        adjacency_tree: dict[node_id -> set of prereq_ids]
    """
    visited = set()
    queue = [target_node_id]
    dependency_tree = {}

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        deps = db.execute('''
            SELECT prerequisite_node_id FROM knowledge_dependency
            WHERE node_id = ?
        ''', (current,)).fetchall()

        for dep in deps:
            prereq_id = dep['prerequisite_node_id']
            if prereq_id not in visited:
                node = db.execute(
                    'SELECT COALESCE(current_mastery, 0.0) as m FROM knowledge_nodes WHERE id = ?',
                    (prereq_id,)
                ).fetchone()
                mastery = node['m'] if node else 0.0
                if mastery < mastery_threshold:
                    queue.append(prereq_id)
                    if current not in dependency_tree:
                        dependency_tree[current] = set()
                    dependency_tree[current].add(prereq_id)

    return dependency_tree


def fetch_all_nodes_mastery_and_names(db, node_ids):
    """
    Given a set of node_ids, return dicts of mastery and name.
    Prefer stored current_mastery; fall back to computing from scratch.
    """
    node_masteries = {}
    node_names = {}
    for node_id in node_ids:
        node = db.execute(
            'SELECT name, subject, COALESCE(current_mastery, 0.0) as mastery FROM knowledge_nodes WHERE id = ?',
            (node_id,)
        ).fetchone()
        if node:
            node_names[node_id] = node['name']
            m = node['mastery']
            if m > 0:
                node_masteries[node_id] = m
            else:
                # compute from scratch
                now = datetime.now(UTC).replace(tzinfo=None)
                avg_retention = get_node_avg_retention(db, node_id, now)
                rolling_acc = get_node_sliding_accuracy(db, node_id)
                avg_lambda = get_node_avg_lambda(db, node_id)
                from practice.adaptive.irt import calc_mastery
                node_masteries[node_id] = calc_mastery(avg_retention, rolling_acc, avg_lambda)
        else:
            node_masteries[node_id] = 0.0

    return node_masteries, node_names


def fetch_all_nodes_irt_theta(db):
    """
    Fetch irt_theta for all knowledge nodes.
    Returns dict: node_id -> irt_theta (default 0.0).
    """
    rows = db.execute(
        'SELECT id, COALESCE(irt_theta, 0.0) as irt_theta FROM knowledge_nodes'
    ).fetchall()
    return {r['id']: r['irt_theta'] for r in rows}
