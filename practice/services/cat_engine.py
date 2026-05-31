"""
CAT (Computer Adaptive Testing) engine — IRT 3PL-based adaptive exam.

Batch-oriented design: fetch all questions upfront, submit all answers
at once.  Pure algorithmic core: receives db connection as first parameter.
"""
import json
from datetime import datetime, UTC

from practice.core.irt import calc_irt_difficulty_damping


def get_all_cat_questions(db, session_id):
    """Fetch up to max_tasks questions ranked by IRT information value.

    Instead of streaming one question per request, this returns the full
    set at once so the client can cache everything and let the student
    navigate freely without further server round-trips.
    """
    session = db.execute(
        "SELECT current_theta, max_tasks FROM cat_exam_sessions WHERE id = ?",
        (session_id,)
    ).fetchone()

    if not session:
        return None, 0

    theta = session['current_theta']
    max_tasks = session['max_tasks']

    all_questions = db.execute(
        "SELECT id, content, answer, image_path, answer_image_path, content_type, irt_a, irt_b, irt_c FROM questions"
    ).fetchall()

    if not all_questions:
        return [], max_tasks

    # Score every question by IRT information: damping * discrimination
    scored = []
    for q in all_questions:
        damping = calc_irt_difficulty_damping(theta, q['irt_b'])
        score = damping * q['irt_a']
        scored.append((score, q))

    # Sort by score descending, take top max_tasks
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[:max_tasks]

    questions = []
    for _, q in selected:
        questions.append({
            'id': q['id'],
            'content': q['content'],
            'answer': q['answer'],
            'content_type': q['content_type'] or 'text',
            'image_url': f"/practice/uploads/{q['image_path']}" if q['image_path'] else '',
            'answer_image_url': f"/practice/uploads/{q['answer_image_path']}" if q['answer_image_path'] else '',
        })

    return questions, max_tasks


def submit_cat_batch(db, session_id, answers):
    """Batch-submit all answers for a CAT session.

    Processes each answer: writes answer_records (with strokes),
    inserts cat_exam_details, and returns full comparison data.

    Args:
        db: SQLite connection
        session_id: int
        answers: list of {question_id, strokes}

    Returns:
        dict with {session_id, task_count, final_responses, msg}
    """
    session = db.execute(
        "SELECT * FROM cat_exam_sessions WHERE id = ?",
        (session_id,)
    ).fetchone()

    if not session:
        return {'error': 'CAT session not found'}

    task_count = 0
    question_ids = []
    responses = []

    for ans in answers:
        qid = ans.get('question_id')
        strokes = ans.get('strokes', [])

        q = db.execute(
            "SELECT content, answer, content_type, image_path, answer_image_path FROM questions WHERE id = ?",
            (qid,)
        ).fetchone()
        if not q:
            continue

        strokes_json = json.dumps(strokes) if strokes else '[]'
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()

        db.execute("""
            INSERT INTO answer_records (question_id, time_spent, is_correct, strokes, session_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (qid, 0, 0, strokes_json, session_id, now))

        db.execute("""
            INSERT INTO cat_exam_details (session_id, question_id, theta_before, theta_after)
            VALUES (?, ?, ?, ?)
        """, (session_id, qid, 0.0, 0.0))

        question_ids.append(qid)
        task_count += 1

        responses.append({
            'question_id': qid,
            'content': q['content'],
            'answer': q['answer'],
            'content_type': q['content_type'] or 'text',
            'image_url': f"/practice/uploads/{q['image_path']}" if q['image_path'] else '',
            'answer_image_url': f"/practice/uploads/{q['answer_image_path']}" if q['answer_image_path'] else '',
            'strokes': strokes,
        })

    # Finalise session
    db.execute(
        "UPDATE cat_exam_sessions SET task_count = ?, question_history = ?, is_completed = 1 WHERE id = ?",
        (task_count, json.dumps(question_ids), session_id)
    )
    db.commit()

    return {
        'session_id': session_id,
        'task_count': task_count,
        'final_responses': responses,
    }
