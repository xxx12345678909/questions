"""
CAT (Computer Adaptive Testing) engine — IRT 3PL-based adaptive exam.

Batch-oriented design: fetch all questions upfront, submit all answers
at once.  Pure algorithmic core: receives db connection as first parameter.
"""
import heapq
import json
from datetime import datetime, UTC

from practice.core.irt import calc_irt_difficulty_damping


def get_all_cat_questions(db, session_id):
    """Fetch up to max_tasks questions ranked by IRT information value.

    Instead of streaming one question per request, this returns the full
    set at once so the client can cache everything and let the student
    navigate freely without further server round-trips.

    [Complexity] Time: O(N log K) — heapq.nlargest with K = max_tasks
                Space: O(K) — only top-K results kept in memory

        Previously O(N log N) via full sort; now O(N log K) where K ≪ N
        (max_tasks is typically 10-30, N can be hundreds).
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
    # Use heapq.nlargest: O(N log K) instead of full sort O(N log N)
    def _irt_score(q):
        damping = calc_irt_difficulty_damping(theta, q['irt_b'])
        return damping * q['irt_a']

    selected = heapq.nlargest(max_tasks, all_questions, key=_irt_score)

    questions = []
    for q in selected:
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

    [Complexity] Time: O(K) where K = len(answers)  Space: O(K)
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

        # theta_before/after start as NULL — will be filled when the record is annotated
        db.execute("""
            INSERT INTO cat_exam_details (session_id, question_id, theta_before, theta_after)
            VALUES (?, ?, NULL, NULL)
        """, (session_id, qid))

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


def recalc_cat_session_theta(db, session_id, just_annotated_record_id=None):
    """Replay all annotated CAT records in order, updating theta_before/after
    and the session's current_theta.

    Called after annotating a CAT answer record via PUT /api/records/<id>.
    Only processes records that have already been annotated (theta_after IS NOT NULL)
    plus the record being annotated right now.

    Also updates the associated knowledge_nodes' irt_theta via a rolling average.

    [Complexity] Time: O(D) where D = number of CAT detail records for this session
                Space: O(1)
    """
    from practice.core.irt import calibrate_irt_parameters

    session = db.execute(
        'SELECT current_theta FROM cat_exam_sessions WHERE id = ?', (session_id,)
    ).fetchone()
    if not session:
        return

    # Fetch all CAT details with answer correctness, ordered by detail id
    details = db.execute('''
        SELECT d.id AS detail_id, d.question_id, r.id AS record_id, r.is_correct,
               COALESCE(q.irt_a, 1.0) AS irt_a,
               COALESCE(q.irt_b, 0.0) AS irt_b,
               COALESCE(q.irt_c, 0.0) AS irt_c
        FROM cat_exam_details d
        JOIN answer_records r ON r.question_id = d.question_id AND r.session_id = d.session_id
        JOIN questions q ON q.id = d.question_id
        WHERE d.session_id = ?
        ORDER BY d.id
    ''', (session_id,)).fetchall()

    current_theta = session['current_theta']

    for d in details:
        # Skip unannotated records (theta_after is still NULL)
        # — unless this is the record being annotated right now
        is_annotated = d['record_id'] == just_annotated_record_id
        if not is_annotated:
            # Check if already annotated in a previous pass
            existing = db.execute(
                'SELECT theta_after FROM cat_exam_details WHERE id = ?',
                (d['detail_id'],)
            ).fetchone()
            if existing and existing['theta_after'] is not None:
                is_annotated = True

        if not is_annotated:
            continue

        theta_before = current_theta
        new_theta, new_a, new_b = calibrate_irt_parameters(
            current_theta, d['irt_a'], d['irt_b'], d['irt_c'], bool(d['is_correct'])
        )

        db.execute('''
            UPDATE cat_exam_details SET theta_before = ?, theta_after = ? WHERE id = ?
        ''', (round(theta_before, 4), round(new_theta, 4), d['detail_id']))

        # Also update question IRT parameters (accumulated from calibration)
        db.execute(
            'UPDATE questions SET irt_a = ?, irt_b = ? WHERE id = ?',
            (new_a, new_b, d['question_id'])
        )

        # Update knowledge_node irt_theta (rolling average toward new_theta)
        node_rows = db.execute(
            'SELECT node_id FROM question_node_mapping WHERE question_id = ?',
            (d['question_id'],)
        ).fetchall()
        for nr in node_rows:
            db.execute(
                'UPDATE knowledge_nodes SET irt_theta = (irt_theta * 0.7 + ? * 0.3) WHERE id = ?',
                (new_theta, nr['node_id'])
            )

        current_theta = new_theta

    db.execute(
        'UPDATE cat_exam_sessions SET current_theta = ? WHERE id = ?',
        (round(current_theta, 4), session_id)
    )
