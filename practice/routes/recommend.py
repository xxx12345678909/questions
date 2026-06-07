"""Recommendation and session routes — adaptive algorithm consumption gateway."""
import json
from datetime import datetime, UTC

from flask import Blueprint, request, jsonify

from practice.db import get_db, get_config, get_config_float, get_config_int, update_node_mastery
from practice.engine import (
    calc_retention, calc_priority,
    update_cost, update_accuracy,
    update_lambda_with_time_cost,
    recommend_questions_advanced,
    calc_fatigue,
    calibrate_irt_parameters,
)

recommend_bp = Blueprint('recommend_api', __name__)


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _fatigue_message(fatigue):
    if fatigue < 0.2:
        return '状态良好，适合挑战高难度题目'
    elif fatigue < 0.4:
        return '轻度疲劳，建议保持中等难度'
    elif fatigue < 0.6:
        return '中度疲劳，推荐基础巩固题'
    elif fatigue < 0.8:
        return '明显疲劳，建议适当休息'
    else:
        return '高度疲劳，强烈建议休息后再继续'


# ----------------------------------------------------------------
# Recommendation
# ----------------------------------------------------------------

@recommend_bp.route('/api/recommend/today', methods=['GET'])
def today_recommend():
    db = get_db()
    budget = request.args.get('budget', type=int, default=0)
    shuffle = request.args.get('shuffle', '').lower() == 'true'

    if budget <= 0:
        budget = get_config_int(db, 'daily_question_budget')

    review_ratio = get_config_float(db, 'review_ratio')
    wrong_ratio = get_config_float(db, 'wrong_ratio')
    new_ratio = get_config_float(db, 'new_ratio')
    threshold = get_config_float(db, 'retention_threshold')
    max_consecutive = get_config_int(db, 'max_consecutive_type')

    fatigue = None
    try:
        session = db.execute(
            'SELECT * FROM user_study_sessions ORDER BY id DESC LIMIT 1'
        ).fetchone()
        if session and session['current_fatigue'] and _session_is_today(session):
            fatigue = session['current_fatigue']
    except Exception:
        pass

    result = recommend_questions_advanced(
        db, budget, review_ratio, wrong_ratio, new_ratio,
        threshold, max_consecutive, shuffle_within=shuffle,
        enable_knowledge_graph=True,
        fatigue=fatigue,
        enable_difficulty_adaptation=True,
        enable_irt=True,
    )

    result['budget'] = budget
    result['msg'] = f'已生成今日推荐，共 {result["total"]} 题'
    if fatigue and fatigue > 0.2:
        result['fatigue_active'] = True
        result['fatigue'] = round(fatigue, 4)
    return jsonify(result)


@recommend_bp.route('/api/recommend/advanced', methods=['GET'])
def recommend_advanced():
    """使用启用知识图谱的推荐引擎生成推荐题目列表。"""
    db = get_db()
    budget = get_config_int(db, 'daily_question_budget') or 20
    review_ratio = get_config_float(db, 'review_ratio') or 0.5
    wrong_ratio = get_config_float(db, 'wrong_ratio') or 0.2
    new_ratio = get_config_float(db, 'new_ratio') or 0.3
    threshold = get_config_float(db, 'retention_threshold') or 0.6
    max_consecutive = get_config_int(db, 'max_consecutive_type') or 3

    enable_kg = request.args.get('enable_knowledge_graph', 'true').lower() == 'true'
    fatigue_param = request.args.get('fatigue', type=float)
    enable_diff_adapt = request.args.get('enable_difficulty_adaptation', 'false').lower() == 'true'
    enable_irt = request.args.get('enable_irt', 'false').lower() == 'true'

    result = recommend_questions_advanced(
        db, budget, review_ratio, wrong_ratio, new_ratio,
        threshold, max_consecutive,
        shuffle_within=False,
        enable_knowledge_graph=enable_kg,
        fatigue=fatigue_param,
        enable_difficulty_adaptation=enable_diff_adapt,
        enable_irt=enable_irt,
    )

    return jsonify(result)


# ----------------------------------------------------------------
# Answer submission
# ----------------------------------------------------------------

@recommend_bp.route('/api/recommend/wrong-reinforce', methods=['GET'])
def wrong_reinforce():
    """
    Wrong-question reinforcement pool.
    Questions with times_wrong >= 2 that haven't yet achieved 3 consecutive correct.
    Sorted by forgetting-curve priority (most urgent first).
    """
    db = get_db()
    threshold = request.args.get('wrong_threshold', type=int, default=2)
    grad_threshold = request.args.get('graduate_threshold', type=int, default=3)
    limit = request.args.get('limit', type=int, default=30)
    now = datetime.now(UTC).replace(tzinfo=None)

    from practice.core.ebbinghaus import calc_score

    rows = db.execute('''
        SELECT q.id, q.content, q.answer, q.subject, q.type, q.difficulty,
               q.avg_cost, q.content_type, q.image_path, q.answer_image_path, q.source,
               s.lambda_, s.last_review, s.times_correct, s.times_wrong,
               COALESCE(s.consecutive_correct, 0) as consecutive_correct
        FROM questions q
        LEFT JOIN user_question_state s ON q.id = s.question_id
        WHERE s.times_wrong >= ?
          AND COALESCE(s.consecutive_correct, 0) < ?
    ''', (threshold, grad_threshold)).fetchall()

    questions = []
    for row in rows:
        lambda_ = row['lambda_'] if row['lambda_'] is not None else 0.3
        times_wrong = row['times_wrong'] if row['times_wrong'] is not None else 0
        subject = row['subject'] or ''
        avg_cost = row['avg_cost']

        score, priority, retention = calc_score(
            lambda_, row['last_review'], subject, times_wrong, avg_cost, now
        )

        questions.append({
            'id': row['id'],
            'content': row['content'],
            'answer': row['answer'],
            'subject': subject,
            'type': row['type'] or '',
            'difficulty': row['difficulty'],
            'avg_cost': row['avg_cost'],
            'source': row['source'] or '',
            'content_type': row['content_type'] or 'text',
            'image_url': f"/practice/uploads/{row['image_path']}" if row['image_path'] else '',
            'answer_image_url': f"/practice/uploads/{row['answer_image_path']}" if row['answer_image_path'] else '',
            'times_wrong': times_wrong,
            'consecutive_correct': row['consecutive_correct'],
            'retention': round(retention, 4),
            'priority': round(priority, 4),
            'score': round(score, 4),
        })

    questions.sort(key=lambda x: x['score'], reverse=True)
    result = questions[:limit]

    return jsonify({
        'questions': result,
        'total': len(result),
        'pool_size': len(questions),
        'threshold': threshold,
        'graduate_threshold': grad_threshold,
    })


@recommend_bp.route('/api/answer', methods=['POST'])
def submit_answer():
    db = get_db()
    data = request.get_json(force=True) or {}

    question_id = data.get('question_id')
    if not question_id:
        return jsonify({'error': '缺少 question_id'}), 400

    # --- Boundary check (read-only, fast) ---
    q_row = db.execute('SELECT id, subject, avg_cost FROM questions WHERE id = ?', (question_id,)).fetchone()
    if not q_row:
        return jsonify({
            'status': 'error',
            'error': f'题库数据穿透：未找到 ID 为 {question_id} 的题目，请检查数据录入完整性。'
        }), 404

    is_correct = bool(data.get('is_correct', False))
    time_spent = max(0.0, float(data.get('time_spent', 0)))
    strokes_raw = data.get('strokes', [])

    # --- Read current state (fast, read-only) ---
    state_row = db.execute(
        'SELECT lambda_, times_correct, times_wrong, last_review FROM user_question_state WHERE question_id = ?',
        (question_id,)
    ).fetchone()

    old_cost = q_row['avg_cost']
    lambda_old = state_row['lambda_'] if state_row else 0.3
    tc_old = state_row['times_correct'] if state_row else 0
    tw_old = state_row['times_wrong'] if state_row else 0

    # --- Pure-math computation (no I/O) ---
    lambda_new = update_lambda_with_time_cost(lambda_old, is_correct, time_spent, old_cost)
    acc_new, tc_new, tw_new = update_accuracy(tc_old, tw_old, is_correct)
    cost_new = update_cost(old_cost, time_spent) if time_spent > 0 else old_cost
    now_iso = datetime.now(UTC).replace(tzinfo=None).isoformat()

    # --- Offload ALL DB writes to the worker thread (HTTP thread returns immediately) ---
    force_sync = request.args.get('sync', '').lower() in ('1', 'true', 'yes')
    node_ids = [
        r['node_id'] for r in
        db.execute('SELECT node_id FROM question_node_mapping WHERE question_id = ?', (question_id,)).fetchall()
    ]

    write_payload = {
        "question_id": question_id,
        "is_correct": is_correct,
        "time_spent": time_spent,
        "strokes": strokes_raw,
        "lambda_new": lambda_new,
        "acc_new": acc_new,
        "tc_new": tc_new,
        "tw_new": tw_new,
        "cost_new": cost_new,
        "now_iso": now_iso,
        "node_ids": node_ids,
        "has_state": state_row is not None,
    }

    if force_sync:
        try:
            _execute_answer_writes(db, write_payload)
        except Exception:
            return jsonify({'error': '提交失败，请重试'}), 500
    else:
        from practice.scheduler.worker import task_queue, GraphTaskType
        try:
            task_queue.put({
                "type": GraphTaskType.UPDATE_NODE_MASTERY,
                "payload": {"write_payload": write_payload},
            }, block=False)
        except Exception:
            try:
                _execute_answer_writes(db, write_payload)
            except Exception:
                return jsonify({'error': '提交失败，请重试'}), 500

    # IRT calibration (read-only compute, fast)
    irt_result = None
    if node_ids:
        try:
            question_irt = db.execute(
                'SELECT COALESCE(irt_a,1.0) as a, COALESCE(irt_b,0.0) as b, COALESCE(irt_c,0.0) as c FROM questions WHERE id=?',
                (question_id,)
            ).fetchone()
            if question_irt:
                # Batch fetch IRT thetas: 1 query instead of len(node_ids) queries
                ph = ','.join('?' * len(node_ids))
                theta_rows = db.execute(
                    f'SELECT COALESCE(irt_theta,0.0) as t FROM knowledge_nodes WHERE id IN ({ph})',
                    tuple(node_ids)
                ).fetchall()
                thetas = [r['t'] for r in theta_rows]
                if thetas:
                    avg_t = sum(thetas) / len(thetas)
                    new_theta, new_a, new_b = calibrate_irt_parameters(
                        avg_t, question_irt['a'], question_irt['b'],
                        question_irt['c'] or 0.0, is_correct
                    )
                    irt_result = {'theta': new_theta, 'irt_a': new_a, 'irt_b': new_b}
        except Exception:
            pass

    # Retention before answer (read-only)
    ret_before = calc_retention(lambda_old, state_row['last_review'] if state_row else None, datetime.now(UTC).replace(tzinfo=None))

    return jsonify({
        'message': '作答已记录',
        'question_id': question_id,
        'lambda_old': round(lambda_old, 4),
        'lambda_new': round(lambda_new, 4),
        'accuracy_new': round(acc_new, 4),
        'cost_old': round(old_cost, 1),
        'cost_new': round(cost_new, 1),
        'retention_before': round(ret_before, 4),
        'irt_calibrated': irt_result,
    })


def _execute_answer_writes(db, p):
    """
    Execute all DB writes for an answer submission (called by worker thread or sync path).

    Uses INSERT ... ON DUPLICATE KEY UPDATE (MySQL) to eliminate the race condition
    where two concurrent requests both see has_state=False and both try to INSERT
    the same question_id.  Retries up to 3 times on deadlock (MySQL error 1213).

    [Complexity] Time: O(N + Q + R) — batch_update_node_masteries replaces
                        per-node SQL with 3 batch queries + 1 batch UPDATE.
                Space: O(N)
    """
    import json as _json
    import time as _time
    from practice.db import DB_TYPE

    qid = p["question_id"]
    is_c = p["is_correct"]
    strokes = _json.dumps(p.get("strokes", [])) if isinstance(p.get("strokes"), (list, dict)) else (p.get("strokes") or "[]")
    nids = p.get("node_ids", [])

    for attempt in range(3):
        try:
            if DB_TYPE == "mysql":
                # MySQL: ON DUPLICATE KEY UPDATE — atomic upsert, no race condition
                db.execute(
                    'INSERT INTO user_question_state (question_id,lambda_,last_review,accuracy,times_correct,times_wrong) '
                    'VALUES (?,?,?,?,?,?) '
                    'ON DUPLICATE KEY UPDATE lambda_=VALUES(lambda_), last_review=VALUES(last_review), '
                    'accuracy=VALUES(accuracy), times_correct=VALUES(times_correct), times_wrong=VALUES(times_wrong)',
                    (qid, p["lambda_new"], p["now_iso"], p["acc_new"], p["tc_new"], p["tw_new"]))
            else:
                if p.get("has_state"):
                    db.execute(
                        'UPDATE user_question_state SET lambda_=?, last_review=?, accuracy=?, times_correct=?, times_wrong=? WHERE question_id=?',
                        (p["lambda_new"], p["now_iso"], p["acc_new"], p["tc_new"], p["tw_new"], qid))
                else:
                    db.execute(
                        'INSERT INTO user_question_state (question_id,lambda_,last_review,accuracy,times_correct,times_wrong) VALUES (?,?,?,?,?,?)',
                        (qid, p["lambda_new"], p["now_iso"], p["acc_new"], p["tc_new"], p["tw_new"]))

            if p.get("time_spent", 0) > 0:
                db.execute('UPDATE questions SET avg_cost=? WHERE id=?', (p["cost_new"], qid))

            db.execute(
                'INSERT INTO answer_records (question_id,time_spent,is_correct,strokes,session_id,user_theta_snapshot,irt_theta_snapshot) VALUES (?,?,?,?,?,?,?)',
                (qid, p["time_spent"], 1 if is_c else 0, strokes, None, None, None))

            # Track consecutive correct for wrong-question reinforcement
            if is_c:
                db.execute(
                    'UPDATE user_question_state SET consecutive_correct = COALESCE(consecutive_correct, 0) + 1 WHERE question_id = ?',
                    (qid,))
            else:
                db.execute(
                    'UPDATE user_question_state SET consecutive_correct = 0 WHERE question_id = ?',
                    (qid,))

            db.commit()

            # Mastery recompute for associated nodes (batch)
            if nids:
                from practice.repository.knowledge_repo import batch_update_node_masteries
                try:
                    batch_update_node_masteries(db, nids)
                except Exception:
                    pass
                try:
                    db.commit()
                except Exception:
                    pass
            return  # success

        except Exception as e:
            err_str = str(e)
            # MySQL deadlock (1213) or lock wait timeout (1205) — retry
            is_retryable = '1213' in err_str or '1205' in err_str or 'Deadlock' in err_str
            if is_retryable and attempt < 2:
                _time.sleep(0.05 * (attempt + 1))  # 50ms, 100ms backoff
                continue
            raise


# ----------------------------------------------------------------
# Question state detail
# ----------------------------------------------------------------

@recommend_bp.route('/api/questions/<int:question_id>/state', methods=['GET'])
def question_state(question_id):
    db = get_db()
    state = db.execute(
        'SELECT * FROM user_question_state WHERE question_id = ?', (question_id,)
    ).fetchone()
    if not state:
        return jsonify({'error': '该题目尚无练习记录'}), 404

    question = db.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
    now = datetime.now(UTC).replace(tzinfo=None)
    ret = calc_retention(state['lambda_'], state['last_review'], now)
    priority, _ = calc_priority(
        state['lambda_'], state['last_review'],
        question['subject'] if question else '',
        state['times_wrong'], now
    )

    recent = db.execute('''
        SELECT * FROM answer_records
        WHERE question_id = ?
        ORDER BY created_at DESC LIMIT 5
    ''', (question_id,)).fetchall()

    return jsonify({
        'question_id': question_id,
        'lambda_': state['lambda_'],
        'last_review': state['last_review'],
        'accuracy': state['accuracy'],
        'times_correct': state['times_correct'],
        'times_wrong': state['times_wrong'],
        'retention_now': round(ret, 4),
        'priority_now': round(priority, 4),
        'recent_records': [{
            'id': r['id'],
            'time_spent': r['time_spent'],
            'is_correct': bool(r['is_correct']),
            'created_at': r['created_at'],
        } for r in recent],
    })


# ----------------------------------------------------------------
# Answer records
# ----------------------------------------------------------------

@recommend_bp.route('/api/records', methods=['GET'])
def list_records():
    db = get_db()
    question_id = request.args.get('question_id', type=int)
    limit = request.args.get('limit', type=int, default=20)
    offset = request.args.get('offset', type=int, default=0)

    base_from = '''FROM answer_records r
        JOIN questions q ON r.question_id = q.id'''
    where_clauses = ''
    params = []

    if question_id:
        where_clauses += ' WHERE r.question_id = ?'
        params.append(question_id)

    total = db.execute(
        f'SELECT COUNT(*) {base_from}{where_clauses}', params
    ).fetchone()[0]

    main_query = f'SELECT r.*, q.content, q.subject, q.type {base_from}{where_clauses}'
    main_query += ' ORDER BY r.created_at DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])

    rows = db.execute(main_query, params).fetchall()

    return jsonify({
        'records': [{
            'id': r['id'],
            'question_id': r['question_id'],
            'content': r['content'][:100] if r['content'] else '',
            'subject': r['subject'],
            'type': r['type'],
            'time_spent': r['time_spent'],
            'is_correct': bool(r['is_correct']),
            'created_at': r['created_at'],
            'strokes': r['strokes'],
        } for r in rows],
        'total': total,
    })


# ----------------------------------------------------------------
# Single record detail + annotation
# ----------------------------------------------------------------

@recommend_bp.route('/api/records/<int:record_id>', methods=['GET'])
def get_record(record_id):
    db = get_db()
    row = db.execute('''
        SELECT r.*, q.content, q.answer, q.content_type, q.image_path,
               q.answer_image_path, q.subject, q.type
        FROM answer_records r JOIN questions q ON r.question_id = q.id
        WHERE r.id = ?
    ''', (record_id,)).fetchone()
    if not row:
        return jsonify({'error': '记录不存在'}), 404

    return jsonify({
        'record': {
            'id': row['id'],
            'question_id': row['question_id'],
            'time_spent': row['time_spent'],
            'is_correct': bool(row['is_correct']),
            'created_at': row['created_at'],
            'strokes': json.loads(row['strokes'] or '[]'),
            'question': {
                'id': row['question_id'],
                'content': row['content'] or '',
                'answer': row['answer'] or '',
                'content_type': row['content_type'] or 'text',
                'image_url': f"/practice/uploads/{row['image_path']}" if row['image_path'] else '',
                'answer_image_url': f"/practice/uploads/{row['answer_image_path']}" if row['answer_image_path'] else '',
                'subject': row['subject'] or '',
                'type': row['type'] or '',
            },
        }
    })


@recommend_bp.route('/api/records/<int:record_id>', methods=['PUT'])
def update_record(record_id):
    db = get_db()
    data = request.get_json(force=True) or {}
    is_correct = data.get('is_correct')
    if is_correct is None:
        return jsonify({'error': '缺少 is_correct'}), 400

    row = db.execute(
        'SELECT question_id, session_id FROM answer_records WHERE id = ?', (record_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': '记录不存在'}), 404

    is_c = bool(is_correct)
    db.execute('UPDATE answer_records SET is_correct = ? WHERE id = ?',
               (1 if is_c else 0, record_id))

    # Recompute user_question_state by replaying all records
    qid = row['question_id']
    records = db.execute(
        'SELECT is_correct, time_spent FROM answer_records WHERE question_id = ? ORDER BY created_at',
        (qid,)
    ).fetchall()

    question = db.execute('SELECT avg_cost FROM questions WHERE id = ?', (qid,)).fetchone()
    cost = question['avg_cost'] if question else 5.0

    lambda_ = 0.3
    tc = 0
    tw = 0
    for r in records:
        is_c2 = bool(r['is_correct'])
        lambda_ = update_lambda_with_time_cost(lambda_, is_c2, r['time_spent'], cost)
        tc += 1 if is_c2 else 0
        tw += 0 if is_c2 else 1

    accuracy = tc / (tc + tw) if (tc + tw) > 0 else 0.5
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()

    existing = db.execute(
        'SELECT question_id FROM user_question_state WHERE question_id = ?', (qid,)
    ).fetchone()
    if existing:
        db.execute('''
            UPDATE user_question_state
            SET lambda_=?, accuracy=?, times_correct=?, times_wrong=?, last_review=?
            WHERE question_id=?
        ''', (lambda_, accuracy, tc, tw, now, qid))
    else:
        db.execute('''
            INSERT INTO user_question_state (question_id, lambda_, accuracy, times_correct, times_wrong, last_review)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (qid, lambda_, accuracy, tc, tw, now))

    # If this record belongs to a CAT session, replay theta chain after annotation
    session_id = row['session_id']
    if session_id:
        cat_row = db.execute(
            'SELECT id FROM cat_exam_sessions WHERE id = ?', (session_id,)
        ).fetchone()
        if cat_row:
            from practice.services.cat_engine import recalc_cat_session_theta
            recalc_cat_session_theta(db, session_id, record_id)

    db.commit()
    return jsonify({'message': '记录已更新', 'record_id': record_id, 'is_correct': is_c})


# ----------------------------------------------------------------
# Stats
# ----------------------------------------------------------------

@recommend_bp.route('/api/stats', methods=['GET'])
def stats():
    db = get_db()

    total_q = db.execute('SELECT COUNT(*) FROM questions').fetchone()[0]
    with_state = db.execute('SELECT COUNT(*) FROM user_question_state').fetchone()[0]
    total_records = db.execute('SELECT COUNT(*) FROM answer_records').fetchone()[0]

    overall = db.execute('''
        SELECT AVG(accuracy) as avg_acc,
               SUM(times_correct) as total_correct,
               SUM(times_wrong) as total_wrong
        FROM user_question_state
    ''').fetchone()

    today = db.execute('''
        SELECT COUNT(*) FROM answer_records
        WHERE DATE(created_at) = DATE('now')
    ''').fetchone()[0]

    avg_time = db.execute('''
        SELECT AVG(time_spent) FROM answer_records
    ''').fetchone()[0] or 0

    subjects = db.execute('''
        SELECT q.subject,
               COUNT(DISTINCT q.id) as total_q,
               AVG(s.accuracy) as avg_acc
        FROM questions q
        LEFT JOIN user_question_state s ON q.id = s.question_id
        WHERE q.subject != ''
        GROUP BY q.subject
    ''').fetchall()

    return jsonify({
        'total_questions': total_q,
        'questions_with_state': with_state,
        'total_records': total_records,
        'overall_accuracy': round(overall['avg_acc'] or 0, 4),
        'total_correct': overall['total_correct'] or 0,
        'total_wrong': overall['total_wrong'] or 0,
        'today_answered': today,
        'avg_time_per_question': round(avg_time, 1),
        'subject_breakdown': {
            s['subject']: {
                'total': s['total_q'],
                'accuracy': round(s['avg_acc'] or 0, 4)
            }
            for s in subjects
        },
    })


# ----------------------------------------------------------------
# Config
# ----------------------------------------------------------------

@recommend_bp.route('/api/config', methods=['GET', 'PUT'])
def config_api():
    db = get_db()
    if request.method == 'GET':
        config = {}
        for k in ['daily_question_budget', 'review_ratio', 'wrong_ratio',
                   'new_ratio', 'retention_threshold', 'max_consecutive_type',
                   'enable_irt']:
            config[k] = get_config(db, k)
        return jsonify(config)

    data = request.get_json(force=True) or {}
    validators = {
        'daily_question_budget': (1, 100),
        'review_ratio': (0.0, 1.0),
        'wrong_ratio': (0.0, 1.0),
        'new_ratio': (0.0, 1.0),
        'retention_threshold': (0.1, 0.9),
        'max_consecutive_type': (1, 20),
    }
    bool_keys = {'enable_irt'}

    for k, v in data.items():
        if k in bool_keys:
            db.execute(
                'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                (k, 'true' if v in (True, 'true', '1', 1) else 'false')
            )
        elif k in validators:
            lo, hi = validators[k]
            val = float(v)
            if val < lo or val > hi:
                return jsonify({'error': f'{k} 需在 {lo}-{hi} 之间'}), 400
            db.execute(
                'INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)',
                (k, str(v))
            )

    db.commit()
    return jsonify({'message': '设置已更新'})


@recommend_bp.route('/api/reset-questions', methods=['POST'])
def reset_all_questions():
    """清空所有作答记录和状态，将所有题目重置为新题"""
    db = get_db()
    db.execute('DELETE FROM answer_records')
    db.execute('DELETE FROM user_question_state')
    db.execute('UPDATE knowledge_nodes SET current_mastery = 0.0, rolling_accuracy = 0.5')
    db.execute('DELETE FROM user_study_sessions')
    db.commit()
    return jsonify({'message': '所有题目已重置为新题，答题记录已清空'})


# ================================================================
# Session fatigue tracking
# ================================================================

@recommend_bp.route('/api/session/status', methods=['GET'])
def session_status():
    """获取当前会话的疲劳度状态。"""
    db = get_db()
    try:
        session = db.execute(
            'SELECT * FROM user_study_sessions ORDER BY id DESC LIMIT 1'
        ).fetchone()

        # Auto-reset: stale cross-day session → treated as inactive
        if not session or not _session_is_today(session):
            return jsonify({
                'active': False,
                'fatigue': 0.0,
                'total_questions': 0,
                'accumulated_minutes': 0.0,
                'message': '当前无活跃会话',
            })

        return jsonify({
            'active': True,
            'session_id': session['id'],
            'session_start': session['session_start'],
            'last_action': session['last_action'],
            'total_questions': session['total_questions'],
            'accumulated_minutes': round(session['accumulated_minutes'], 1),
            'current_fatigue': round(session['current_fatigue'], 4),
            'message': _fatigue_message(session['current_fatigue']),
        })
    except Exception:
        return jsonify({
            'active': False,
            'fatigue': 0.0,
            'message': '会话跟踪暂不可用',
        })


def _session_is_today(session):
    """Check if a session started on today's date."""
    if not session or not session['session_start']:
        return False
    session_date = session['session_start'][:10]
    today = datetime.now(UTC).replace(tzinfo=None).isoformat()[:10]
    return session_date == today


@recommend_bp.route('/api/session/start', methods=['POST'])
def session_start():
    """开始新的学习会话。"""
    db = get_db()
    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    try:
        db.execute(
            'INSERT INTO user_study_sessions (session_start, last_action, total_questions, accumulated_minutes, current_fatigue) VALUES (?, ?, 0, 0.0, 0.0)',
            (now, now)
        )
        db.commit()
        return jsonify({'message': '新会话已开始', 'session_start': now})
    except Exception:
        return jsonify({'message': '会话已就绪（跟踪表暂不可用）'})


@recommend_bp.route('/api/session/update', methods=['POST'])
def session_update():
    """更新当前会话状态（答题数、耗时、疲劳度）。"""
    db = get_db()
    data = request.get_json(force=True) or {}
    try:
        session = db.execute(
            'SELECT * FROM user_study_sessions ORDER BY id DESC LIMIT 1'
        ).fetchone()

        if not session:
            return jsonify({'error': '无活跃会话，请先开始新会话'}), 400

        time_spent_minutes = float(data.get('time_spent', 0)) / 60.0
        new_questions = (session['total_questions'] or 0) + 1
        new_minutes = (session['accumulated_minutes'] or 0.0) + time_spent_minutes
        fatigue = calc_fatigue(new_minutes, new_questions)

        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        db.execute(
            'UPDATE user_study_sessions SET total_questions = ?, accumulated_minutes = ?, current_fatigue = ?, last_action = ? WHERE id = ?',
            (new_questions, round(new_minutes, 1), round(fatigue, 4), now, session['id'])
        )
        db.commit()

        question_id = data.get('question_id')
        if question_id:
            try:
                nodes = db.execute(
                    'SELECT node_id FROM question_node_mapping WHERE question_id = ?',
                    (question_id,)
                ).fetchall()
                for n in nodes:
                    nid = n['node_id']
                    from practice.repository.cache_proxy import cache_service
                    cache_service.lpush_fixed_window(
                        f"practice:node:theta_window:{nid}", 1
                    )
                    from practice.scheduler.worker import task_queue, GraphTaskType
                    try:
                        task_queue.put({
                            "type": GraphTaskType.UPDATE_NODE_MASTERY,
                            "payload": {"node_id": nid, "question_id": question_id},
                        }, block=False)
                    except Exception:
                        pass    # queue full — silently degrade
            except Exception:
                pass

        return jsonify({
            'total_questions': new_questions,
            'accumulated_minutes': round(new_minutes, 1),
            'current_fatigue': round(fatigue, 4),
            'message': _fatigue_message(fatigue),
        })
    except Exception as e:
        return jsonify({'error': f'会话更新失败: {str(e)}'}), 500


# ================================================================
# Daily / Weekly Report
# ================================================================

@recommend_bp.route('/api/report/daily', methods=['GET'])
def daily_report():
    """Today's study report: stats, subject breakdown, weakest nodes."""
    db = get_db()
    now = datetime.now(UTC).replace(tzinfo=None)
    today = now.strftime('%Y-%m-%d')

    # Today's answers
    today_stats = db.execute('''
        SELECT COUNT(*) as cnt,
               SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct,
               SUM(time_spent) as total_sec,
               AVG(time_spent) as avg_sec
        FROM answer_records
        WHERE DATE(created_at) = DATE('now')
    ''').fetchone()

    today_cnt = today_stats['cnt'] or 0
    today_correct = today_stats['correct'] or 0
    today_accuracy = round(today_correct / today_cnt * 100, 1) if today_cnt > 0 else 0
    today_minutes = round((today_stats['total_sec'] or 0) / 60.0, 1)

    # This week's daily counts (last 7 days)
    week_daily = db.execute('''
        SELECT DATE(created_at) as day, COUNT(*) as cnt,
               SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) as correct
        FROM answer_records
        WHERE created_at >= DATE('now', '-6 days')
        GROUP BY DATE(created_at)
        ORDER BY day
    ''').fetchall()

    week_data = {}
    for r in week_daily:
        week_data[r['day']] = {'cnt': r['cnt'], 'correct': r['correct']}

    # Fill in missing days
    from datetime import timedelta
    days = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime('%Y-%m-%d')
        wd = week_data.get(d, {'cnt': 0, 'correct': 0})
        days.append({
            'date': d,
            'cnt': wd['cnt'],
            'correct': wd['correct'],
            'is_today': d == today,
        })

    # Weakest knowledge nodes (by mastery)
    weak_nodes = db.execute('''
        SELECT id, name, subject, current_mastery as mastery, rolling_accuracy as accuracy
        FROM knowledge_nodes
        ORDER BY COALESCE(current_mastery, 0) ASC
        LIMIT 5
    ''').fetchall()

    # Subject breakdown
    subjects = db.execute('''
        SELECT q.subject,
               COUNT(DISTINCT q.id) as total_q,
               COUNT(DISTINCT ar.id) as answered,
               AVG(s.accuracy) as avg_acc,
               AVG(s.lambda_) as avg_lambda
        FROM questions q
        LEFT JOIN user_question_state s ON q.id = s.question_id
        LEFT JOIN answer_records ar ON ar.question_id = q.id
        WHERE q.subject != ''
        GROUP BY q.subject
    ''').fetchall()

    # Total questions answered ever
    total_answered = db.execute(
        'SELECT COUNT(DISTINCT question_id) FROM answer_records'
    ).fetchone()[0]

    total_questions = db.execute('SELECT COUNT(*) FROM questions').fetchone()[0]

    return jsonify({
        'today': {
            'date': today,
            'answered': today_cnt,
            'correct': today_correct,
            'accuracy': today_accuracy,
            'minutes': today_minutes,
        },
        'week': days,
        'weak_nodes': [{
            'id': n['id'],
            'name': n['name'],
            'subject': n['subject'],
            'mastery': round(n['mastery'] or 0, 2),
            'accuracy': round(n['accuracy'] or 0, 2),
        } for n in weak_nodes],
        'subjects': [{
            'subject': s['subject'],
            'total': s['total_q'],
            'answered': s['answered'],
            'accuracy': round(s['avg_acc'] or 0, 2),
            'lambda': round(s['avg_lambda'] or 0.3, 2),
        } for s in subjects],
        'overview': {
            'total_questions': total_questions,
            'total_answered': total_answered,
            'coverage': round(total_answered / total_questions * 100, 1) if total_questions > 0 else 0,
        },
    })
