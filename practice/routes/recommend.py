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

@recommend_bp.route('/api/answer', methods=['POST'])
def submit_answer():
    db = get_db()
    data = request.get_json(force=True) or {}

    question_id = data.get('question_id')
    if not question_id:
        return jsonify({'error': '缺少 question_id'}), 400

    is_correct = bool(data.get('is_correct', False))
    time_spent = max(0.0, float(data.get('time_spent', 0)))
    strokes = json.dumps(data.get('strokes', []))

    question = db.execute(
        'SELECT * FROM questions WHERE id = ?', (question_id,)
    ).fetchone()
    if not question:
        return jsonify({'error': '题目不存在'}), 404

    state = db.execute(
        'SELECT * FROM user_question_state WHERE question_id = ?', (question_id,)
    ).fetchone()

    lambda_old = state['lambda_'] if state else 0.3
    old_cost = question['avg_cost']

    if state:
        lambda_new = update_lambda_with_time_cost(state['lambda_'], is_correct, time_spent, old_cost)
        acc_new, tc, tw = update_accuracy(
            state['times_correct'], state['times_wrong'], is_correct
        )
        db.execute('''
            UPDATE user_question_state
            SET lambda_=?, last_review=?, accuracy=?, times_correct=?, times_wrong=?
            WHERE question_id=?
        ''', (lambda_new, datetime.now(UTC).replace(tzinfo=None).isoformat(), acc_new, tc, tw, question_id))
    else:
        lambda_new = update_lambda_with_time_cost(0.3, is_correct, time_spent, old_cost)
        acc_new, tc, tw = update_accuracy(0, 0, is_correct)
        db.execute('''
            INSERT INTO user_question_state
            (question_id, lambda_, last_review, accuracy, times_correct, times_wrong)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (question_id, lambda_new, datetime.now(UTC).replace(tzinfo=None).isoformat(), acc_new, tc, tw))

    if time_spent > 0:
        cost_new = update_cost(old_cost, time_spent)
        db.execute('UPDATE questions SET avg_cost = ? WHERE id = ?', (cost_new, question_id))
    else:
        cost_new = old_cost

    db.execute('''
        INSERT INTO answer_records (question_id, time_spent, is_correct, strokes, session_id, user_theta_snapshot, irt_theta_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (question_id, time_spent, 1 if is_correct else 0, strokes, None, None, None))

    db.commit()

    # Update mastery for all associated knowledge nodes (async)
    try:
        node_rows = db.execute(
            'SELECT node_id FROM question_node_mapping WHERE question_id = ?',
            (question_id,)
        ).fetchall()
        for nr in node_rows:
            nid = nr['node_id']
            # 1. Fast in-memory sliding-window correctness update
            from practice.repository.cache_proxy import cache_service
            cache_service.lpush_fixed_window(
                f"practice:node:theta_window:{nid}", 1 if is_correct else 0
            )
            # 2. Offload heavy persistence recompute to background worker
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

    # v5: IRT 3PL parameter calibration — update theta/a/b per associated node
    irt_result = None
    try:
        node_rows = db.execute(
            'SELECT node_id FROM question_node_mapping WHERE question_id = ?',
            (question_id,)
        ).fetchall()
        question_irt = db.execute(
            'SELECT COALESCE(irt_a, 1.0) as a, COALESCE(irt_b, 0.0) as b, COALESCE(irt_c, 0.0) as c FROM questions WHERE id = ?',
            (question_id,)
        ).fetchone()
        if question_irt and node_rows:
            new_irt_a = question_irt['a']
            new_irt_b = question_irt['b']
            irt_c = question_irt['c'] if question_irt['c'] is not None else 0.0

            # Use average theta across all associated nodes
            avg_theta = 0.0
            count = 0
            for nr in node_rows:
                node = db.execute(
                    'SELECT COALESCE(irt_theta, 0.0) as theta FROM knowledge_nodes WHERE id = ?',
                    (nr['node_id'],)
                ).fetchone()
                if node:
                    avg_theta += node['theta']
                    count += 1

            if count > 0:
                avg_theta /= count
                new_theta, new_a, new_b = calibrate_irt_parameters(
                    avg_theta, new_irt_a, new_irt_b, irt_c, is_correct
                )

                # Persist updated IRT parameters
                for nr in node_rows:
                    db.execute(
                        'UPDATE knowledge_nodes SET irt_theta = ? WHERE id = ?',
                        (new_theta, nr['node_id'])
                    )
                db.execute(
                    'UPDATE questions SET irt_a = ?, irt_b = ? WHERE id = ?',
                    (new_a, new_b, question_id)
                )

                # Write irt_theta_snapshot to answer_records
                db.execute(
                    'UPDATE answer_records SET irt_theta_snapshot = ? WHERE id = (SELECT MAX(id) FROM answer_records WHERE question_id = ?)',
                    (new_theta, question_id)
                )
                db.commit()

                irt_result = {
                    'theta': new_theta,
                    'irt_a': new_a,
                    'irt_b': new_b,
                }
    except Exception:
        pass

    now = datetime.now(UTC).replace(tzinfo=None)
    ret_before = calc_retention(
        lambda_old,
        state['last_review'] if state else None,
        now
    )

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
        } for r in rows],
        'total': total,
    })


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
