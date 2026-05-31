"""CAT (Computer Adaptive Testing) routes."""
import json

from flask import Blueprint, request, jsonify

from practice.db import get_db
from practice.services.cat_engine import get_all_cat_questions, submit_cat_batch

cat_bp = Blueprint('cat_api', __name__)


# ----------------------------------------------------------------
# POST /api/cat/session/start
# ----------------------------------------------------------------

@cat_bp.route('/api/cat/session/start', methods=['POST'])
def cat_session_start():
    """Start a new adaptive CAT exam session."""
    db = get_db()
    data = request.get_json(force=True) or {}

    user_id = data.get('user_id', 1)
    max_tasks = int(data.get('max_tasks', 20))

    if max_tasks < 5 or max_tasks > 100:
        return jsonify({'error': 'max_tasks 应在 5-100 之间'}), 400

    try:
        row = db.execute(
            'SELECT AVG(COALESCE(irt_theta, 0.0)) as avg_theta FROM knowledge_nodes'
        ).fetchone()
        initial_theta = round(row['avg_theta'] or 0.0, 4) if row else 0.0
    except Exception:
        initial_theta = 0.0

    cursor = db.execute(
        """INSERT INTO cat_exam_sessions (user_id, current_theta, max_tasks)
           VALUES (?, ?, ?)""",
        (user_id, initial_theta, max_tasks)
    )
    db.commit()

    return jsonify({
        'session_id': cursor.lastrowid,
        'status': 'STARTED',
        'initial_theta': initial_theta,
        'max_tasks': max_tasks,
    })


# ----------------------------------------------------------------
# GET /api/cat/session/<int:session_id>/questions
# ----------------------------------------------------------------

@cat_bp.route('/api/cat/session/<int:session_id>/questions', methods=['GET'])
def cat_session_questions(session_id):
    """Return all CAT questions at once so the client can cache them."""
    db = get_db()

    questions, max_tasks = get_all_cat_questions(db, session_id)

    if questions is None:
        return jsonify({'error': 'CAT 会话不存在'}), 404
    if not questions:
        return jsonify({'error': '题库为空，无法抽题'}), 400

    return jsonify({
        'session_id': session_id,
        'max_tasks': max_tasks,
        'questions': questions,
    })


# ----------------------------------------------------------------
# POST /api/cat/session/submit-all
# ----------------------------------------------------------------

@cat_bp.route('/api/cat/session/submit-all', methods=['POST'])
def cat_session_submit_all():
    """Batch submit all answers (strokes + question IDs) for a CAT session."""
    db = get_db()
    data = request.get_json(force=True) or {}

    session_id = data.get('session_id')
    answers = data.get('answers', [])

    if not session_id:
        return jsonify({'error': '缺少 session_id'}), 400
    if not answers:
        return jsonify({'error': '缺少 answers 数组'}), 400

    result = submit_cat_batch(db, session_id, answers)

    if 'error' in result:
        return jsonify(result), 404

    return jsonify({
        'session_completed': True,
        'session_id': session_id,
        'task_count': result['task_count'],
        'results': result['final_responses'],
        'msg': f'交卷成功！共 {result["task_count"]} 题已生成答题记录。',
    })


# ----------------------------------------------------------------
# GET /api/cat/sessions
# ----------------------------------------------------------------

@cat_bp.route('/api/cat/sessions', methods=['GET'])
def cat_list_sessions():
    """List completed CAT exam sessions with summary."""
    db = get_db()
    limit = request.args.get('limit', type=int, default=10)

    rows = db.execute('''
        SELECT s.id, s.max_tasks, s.task_count, s.is_completed,
               s.created_at, s.current_theta,
               (SELECT COUNT(*) FROM answer_records WHERE session_id = s.id) as record_count
        FROM cat_exam_sessions s
        WHERE s.is_completed = 1
        ORDER BY s.created_at DESC
        LIMIT ?
    ''', (limit,)).fetchall()

    return jsonify({
        'sessions': [{
            'id': r['id'],
            'max_tasks': r['max_tasks'],
            'task_count': r['task_count'],
            'current_theta': r['current_theta'],
            'record_count': r['record_count'],
            'created_at': r['created_at'],
        } for r in rows],
    })


# ----------------------------------------------------------------
# GET /api/cat/session/<int:session_id>/records
# ----------------------------------------------------------------

@cat_bp.route('/api/cat/session/<int:session_id>/records', methods=['GET'])
def cat_session_records(session_id):
    """Return all answer records for a CAT session with full question data."""
    db = get_db()

    session = db.execute(
        'SELECT * FROM cat_exam_sessions WHERE id = ? AND is_completed = 1',
        (session_id,)
    ).fetchone()
    if not session:
        return jsonify({'error': '模考记录不存在或未完成'}), 404

    rows = db.execute('''
        SELECT r.id, r.question_id, r.is_correct, r.time_spent, r.strokes, r.created_at,
               q.content, q.answer, q.content_type, q.image_path, q.answer_image_path, q.subject, q.type
        FROM answer_records r
        JOIN questions q ON r.question_id = q.id
        WHERE r.session_id = ?
        ORDER BY r.id
    ''', (session_id,)).fetchall()

    return jsonify({
        'session_id': session_id,
        'task_count': session['task_count'],
        'max_tasks': session['max_tasks'],
        'records': [{
            'id': r['id'],
            'question_id': r['question_id'],
            'is_correct': bool(r['is_correct']),
            'time_spent': r['time_spent'],
            'strokes': json.loads(r['strokes'] or '[]'),
            'created_at': r['created_at'],
            'question': {
                'id': r['question_id'],
                'content': r['content'] or '',
                'answer': r['answer'] or '',
                'content_type': r['content_type'] or 'text',
                'image_url': f"/practice/uploads/{r['image_path']}" if r['image_path'] else '',
                'answer_image_url': f"/practice/uploads/{r['answer_image_path']}" if r['answer_image_path'] else '',
                'subject': r['subject'] or '',
                'type': r['type'] or '',
            },
        } for r in rows],
    })
