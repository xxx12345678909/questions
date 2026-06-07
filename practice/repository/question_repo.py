"""
Question repository — SQL extraction layer for questions and config.
Receives db connection as first parameter (Flask g-based connection model).
"""
from practice import DEFAULT_CONFIG


# ================================================================
# Config helpers
# ================================================================

def get_config(db, key):
    """
    [Complexity] Time: O(1) — single indexed row lookup  Space: O(1)
    """
    row = db.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
    if row:
        return row['value']
    return DEFAULT_CONFIG.get(key, '')


def get_config_float(db, key):
    return float(get_config(db, key))


def get_config_int(db, key):
    return int(get_config(db, key))


# ================================================================
# Bulk question fetch
# ================================================================

def fetch_all_questions_with_state(db):
    """
    Fetch all questions joined with user_question_state.
    Includes IRT 3PL columns (irt_a, irt_b, irt_c).
    Returns list of sqlite3.Row objects.

    [Complexity] Time: O(N) — single LEFT JOIN full scan  Space: O(N) — result rows
    """
    return db.execute('''
        SELECT q.id, q.content, q.answer, q.subject, q.type, q.difficulty,
               q.avg_cost, q.source, q.content_type, q.image_path, q.answer_image_path,
               s.lambda_, s.last_review, s.accuracy,
               s.times_correct, s.times_wrong,
               COALESCE(q.irt_a, 1.0) as irt_a,
               COALESCE(q.irt_b, 0.0) as irt_b,
               COALESCE(q.irt_c, 0.0) as irt_c
        FROM questions q
        LEFT JOIN user_question_state s ON q.id = s.question_id
    ''').fetchall()


def fetch_question_node_ids(db, question_id):
    """
    Fetch knowledge node IDs associated with a question.

    [Complexity] Time: O(k) where k = number of node mappings  Space: O(k)
    """
    rows = db.execute(
        'SELECT node_id FROM question_node_mapping WHERE question_id = ?',
        (question_id,)
    ).fetchall()
    return [r['node_id'] for r in rows]
