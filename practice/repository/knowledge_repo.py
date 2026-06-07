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
    """Fetch retention values for all prerequisite knowledge nodes of a question.

    DEPRECATED in hot loops — use batch_get_prerequisite_retentions() instead.
    Kept for backward compatibility with infrequent single-question lookups.
    """
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


def batch_get_prerequisite_retentions(db, question_ids, now=None):
    """
    Batch-preload prerequisite retention data for multiple questions.

    Replaces N individual get_prerequisite_retentions() calls (each firing
    nested SQL queries) with 3 upfront queries + in-memory computation.

    Args:
        db: database connection
        question_ids: iterable of question IDs to preload
        now: reference datetime for retention calculation

    Returns:
        dict: question_id -> list of average retention rates per prerequisite node

    [Complexity] Time: O(M + D + S) — single pass over 3 result sets
                Space: O(M + D + S) — question→node map, node→prereq map,
                       prereq→state lists

        M = total question_node_mapping rows
        D = total knowledge_dependency rows
        S = total user_question_state rows linked to any knowledge node
    """
    if now is None:
        now = datetime.now(UTC).replace(tzinfo=None)

    qid_set = set(question_ids)
    if not qid_set:
        return {}

    # ---- Query 1: all question → node mappings for the requested questions ----
    placeholders = ','.join('?' * len(qid_set))
    qnm_rows = db.execute(
        f'SELECT question_id, node_id FROM question_node_mapping WHERE question_id IN ({placeholders})',
        tuple(qid_set)
    ).fetchall()

    # question_id → set of direct node_ids
    q_to_nodes = {}
    for r in qnm_rows:
        q_to_nodes.setdefault(r['question_id'], set()).add(r['node_id'])

    # ---- Query 2: all node → prerequisite dependencies (global, cheap) ----
    dep_rows = db.execute(
        'SELECT node_id, prerequisite_node_id FROM knowledge_dependency'
    ).fetchall()

    # node_id → set of prerequisite node_ids
    node_to_prereqs = {}
    all_prereq_ids = set()
    for r in dep_rows:
        node_to_prereqs.setdefault(r['node_id'], set()).add(r['prerequisite_node_id'])
        all_prereq_ids.add(r['prerequisite_node_id'])

    # ---- Query 3: user_question_state for all questions on prerequisite nodes ----
    if not all_prereq_ids:
        # No prerequisites exist at all — every question gets []
        return {qid: [] for qid in qid_set}

    prereq_placeholders = ','.join('?' * len(all_prereq_ids))
    state_rows = db.execute(
        f'''SELECT s.lambda_, s.last_review, m.node_id
            FROM user_question_state s
            JOIN question_node_mapping m ON s.question_id = m.question_id
            WHERE m.node_id IN ({prereq_placeholders})''',
        tuple(all_prereq_ids)
    ).fetchall()

    # prerequisite_node_id → list of (lambda, last_review)
    prereq_to_states = {}
    for r in state_rows:
        prereq_to_states.setdefault(r['node_id'], []).append((r['lambda_'], r['last_review']))

    # ---- Compute: for each question, collect prerequisite retention values ----
    result = {}
    for qid in qid_set:
        node_ids = q_to_nodes.get(qid, set())
        if not node_ids:
            result[qid] = []
            continue

        retentions = []
        seen_prereqs = set()  # deduplicate across multiple nodes of the same question
        for nid in node_ids:
            for prereq_id in node_to_prereqs.get(nid, set()):
                if prereq_id in seen_prereqs:
                    continue
                seen_prereqs.add(prereq_id)

                states = prereq_to_states.get(prereq_id, [])
                if states:
                    avg_r = sum(
                        _calc_retention_local(lam, lr, now) for lam, lr in states
                    ) / len(states)
                else:
                    avg_r = 0.8  # default for nodes with no practice history
                retentions.append(avg_r)
        result[qid] = retentions

    return result

# [Complexity] get_prerequisite_retentions: Time O(Q * D * P), Space O(P)
#   Q = question_node_mapping entries for this question
#   D = dependency rows per node
#   P = questions linked to each prerequisite node
#   Each iteration fires 1-2 SQL queries — heavily N+1 in the hot loop.
#
# TODO[perf]: Replace triple-nested SQL loop with a single batch query joining
#   question_node_mapping → knowledge_dependency → user_question_state, then
#   aggregate retention per prerequisite node in Python.


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

# [Complexity] get_node_avg_retention: Time O(Q), Space O(1)
#   Scans all questions linked to a knowledge node via JOIN.


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

# [Complexity] get_node_sliding_accuracy: Time O(window_size), Space O(1)
#   SQL LIMIT ensures only window_size rows are fetched.


def get_node_avg_lambda(db, node_id):
    """Get average lambda (forgetting rate) for a knowledge node."""
    row = db.execute('''
        SELECT AVG(s.lambda_) as avg_lambda
        FROM user_question_state s
        JOIN question_node_mapping m ON s.question_id = m.question_id
        WHERE m.node_id = ?
    ''', (node_id,)).fetchone()
    return (row['avg_lambda'] or 0.3) if row else 0.3

# [Complexity] get_node_avg_lambda: Time O(Q), Space O(1)
#   Single SQL AVG aggregation — efficient at the database level.


def update_node_mastery(db, node_id):
    """Recalculate and persist knowledge node mastery + rolling accuracy.

    For multiple nodes prefer batch_update_node_masteries() — it replaces
    N×3 SQL queries with 3 batch queries + 1 batch UPDATE.

    NOTE: This function does NOT call db.commit(). The caller is responsible
    for committing after all mastery updates in the batch are complete.

    [Complexity] Time: O(Q) — 3 aggregate SQL queries over all questions linked
                        to this knowledge node
                Space: O(1)
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
    except Exception:
        pass


def batch_update_node_masteries(db, node_ids):
    """
    Batch-recompute mastery for multiple knowledge nodes.

    Replaces N calls to update_node_mastery() (3N SQL queries) with 3 batch
    queries + 1 batch UPDATE — used by worker debounce and _execute_answer_writes.

    [Complexity] Time: O(N + Q + R) — Q = total state rows, R = total answer rows
                        across all requested nodes. Single pass over each result set.
                Space: O(N + Q + R)
    """
    from practice.adaptive.irt import calc_mastery

    nid_set = set(node_ids)
    if not nid_set:
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    placeholders = ','.join('?' * len(nid_set))
    params = tuple(nid_set)

    # --- Query 1: avg retention per node ---
    retention_rows = db.execute(
        f'''SELECT m.node_id, s.lambda_, s.last_review
            FROM user_question_state s
            JOIN question_node_mapping m ON s.question_id = m.question_id
            WHERE m.node_id IN ({placeholders})''',
        params
    ).fetchall()

    node_retention = {nid: [] for nid in nid_set}
    for r in retention_rows:
        ret = _calc_retention_local(r['lambda_'], r['last_review'], now)
        node_retention[r['node_id']].append(ret)

    # --- Query 2: sliding accuracy per node (window = 5) ---
    # Per-node LIMIT 5 avoids scanning 5000+ answer_records — each node
    # query returns at most 5 rows instead of fetching everything.
    node_accuracy = {nid: [] for nid in nid_set}
    for nid in nid_set:
        rows = db.execute(
            '''SELECT ar.is_correct
               FROM question_node_mapping qnm
               JOIN answer_records ar ON ar.question_id = qnm.question_id
               WHERE qnm.node_id = ?
               ORDER BY ar.created_at DESC
               LIMIT 5''',
            (nid,)
        ).fetchall()
        node_accuracy[nid] = [bool(r['is_correct']) for r in rows]

    # --- Query 3: avg lambda per node ---
    lambda_rows = db.execute(
        f'''SELECT m.node_id, AVG(s.lambda_) as avg_lambda
            FROM user_question_state s
            JOIN question_node_mapping m ON s.question_id = m.question_id
            WHERE m.node_id IN ({placeholders})
            GROUP BY m.node_id''',
        params
    ).fetchall()

    node_lambda = {r['node_id']: (r['avg_lambda'] or 0.3) for r in lambda_rows}

    # --- Compute mastery + batch UPDATE (sorted to prevent InnoDB deadlocks) ---
    for nid in sorted(nid_set):
        try:
            rets = node_retention.get(nid, [])
            accs = node_accuracy.get(nid, [])
            lam = node_lambda.get(nid, 0.3)

            avg_ret = sum(rets) / len(rets) if rets else 0.8
            rolling_acc = sum(1 for c in accs if c) / len(accs) if accs else 0.5
            mastery = calc_mastery(avg_ret, rolling_acc, lam)

            db.execute(
                'UPDATE knowledge_nodes SET current_mastery = ?, rolling_accuracy = ? WHERE id = ?',
                (round(mastery, 4), round(rolling_acc, 4), nid)
            )
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

    [Complexity] Time: O(V + E) — BFS visits reachable nodes below threshold
                Space: O(V + E) — adjacency tree + visited set + deque

        Uses collections.deque for O(1) popleft (was list.pop(0) O(n)).
    """
    from collections import deque

    visited = set()
    queue = deque([target_node_id])
    dependency_tree = {}

    while queue:
        current = queue.popleft()
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

    [Complexity] Time: O(N * Q) worst-case — each node with m=0 triggers 3 SQL
                        aggregate queries (retention, accuracy, lambda)
                Space: O(N)
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

    [Complexity] Time: O(N) — single full-table scan  Space: O(N) — result dict
    """
    rows = db.execute(
        'SELECT id, COALESCE(irt_theta, 0.0) as irt_theta FROM knowledge_nodes'
    ).fetchall()
    return {r['id']: r['irt_theta'] for r in rows}
