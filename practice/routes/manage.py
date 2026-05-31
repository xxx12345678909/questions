"""Question management routes — CRUD, image/PDF upload, unattributed pool."""
import json
import os
import uuid

from flask import Blueprint, request, jsonify, render_template, send_from_directory

from practice import UPLOADS_FOLDER
from practice.db import get_db

manage_bp = Blueprint('manage', __name__)


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _row_to_question(row):
    """Convert a DB row to a dict safe for JSON serialisation."""
    content_type = row['content_type'] if 'content_type' in row.keys() else 'text'
    image_path = row['image_path'] if 'image_path' in row.keys() else ''
    answer_image_path = row['answer_image_path'] if 'answer_image_path' in row.keys() else ''
    return {
        'id': row['id'],
        'content': row['content'],
        'answer': row['answer'],
        'subject': row['subject'],
        'type': row['type'],
        'difficulty': row['difficulty'],
        'avg_cost': row['avg_cost'],
        'source': row['source'],
        'created_at': row['created_at'],
        'content_type': content_type,
        'image_url': f'/practice/uploads/{image_path}' if content_type == 'image' and image_path else '',
        'answer_image_url': f'/practice/uploads/{answer_image_path}' if answer_image_path else '',
    }


# ----------------------------------------------------------------
# Page
# ----------------------------------------------------------------

@manage_bp.route('/')
def index():
    return render_template('practice.html')


# ----------------------------------------------------------------
# File serving & image upload
# ----------------------------------------------------------------

@manage_bp.route('/uploads/<filename>')
def serve_upload(filename):
    return send_from_directory(UPLOADS_FOLDER, filename)


@manage_bp.route('/api/upload/image', methods=['POST'])
def upload_image_question():
    """Upload an image as a question. Accepts image file + metadata."""
    if 'image' not in request.files:
        return jsonify({'error': '请选择图片'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': '请选择图片'}), 400

    ext = os.path.splitext(file.filename)[1] or '.png'
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOADS_FOLDER, filename)
    file.save(filepath)

    answer_image_filename = ''
    if 'answer_image' in request.files:
        afile = request.files['answer_image']
        if afile.filename != '':
            aext = os.path.splitext(afile.filename)[1] or '.png'
            answer_image_filename = f"{uuid.uuid4().hex}{aext}"
            afile.save(os.path.join(UPLOADS_FOLDER, answer_image_filename))

    subject = request.form.get('subject', '').strip()
    qtype = request.form.get('type', '').strip()
    difficulty = max(0.0, min(1.0, float(request.form.get('difficulty', 0.5))))
    avg_cost = max(1.0, min(60.0, float(request.form.get('avg_cost', 5.0))))
    answer = request.form.get('answer', '').strip()

    db = get_db()
    cursor = db.execute('''
        INSERT INTO questions (content, answer, subject, type, difficulty, avg_cost, source, content_type, image_path, answer_image_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'image', ?, ?)
    ''', ('', answer, subject, qtype, difficulty, avg_cost, 'upload', filename, answer_image_filename))
    db.commit()

    result = {
        'id': cursor.lastrowid,
        'message': '图片题目已创建',
        'image_url': f'/practice/uploads/{filename}',
    }
    if answer_image_filename:
        result['answer_image_url'] = f'/practice/uploads/{answer_image_filename}'
    return jsonify(result)


# ----------------------------------------------------------------
# Question CRUD
# ----------------------------------------------------------------

@manage_bp.route('/api/questions', methods=['GET'])
def list_questions():
    db = get_db()
    subject = request.args.get('subject', '')
    qtype = request.args.get('type', '')
    difficulty_min = request.args.get('difficulty_min', type=float)
    difficulty_max = request.args.get('difficulty_max', type=float)
    random_order = request.args.get('random', '').lower() == 'true'
    limit = request.args.get('limit', type=int, default=0)
    search = request.args.get('search', '')
    offset = request.args.get('offset', type=int, default=0)

    base_from = '''FROM questions q
        LEFT JOIN user_question_state s ON q.id = s.question_id
        WHERE 1=1'''
    where_clauses = ''
    params = []

    if subject:
        where_clauses += ' AND q.subject = ?'
        params.append(subject)
    if qtype:
        where_clauses += ' AND q.type = ?'
        params.append(qtype)
    if difficulty_min is not None:
        where_clauses += ' AND q.difficulty >= ?'
        params.append(difficulty_min)
    if difficulty_max is not None:
        where_clauses += ' AND q.difficulty <= ?'
        params.append(difficulty_max)
    if search:
        where_clauses += ' AND q.content LIKE ?'
        params.append(f'%{search}%')

    total = db.execute(
        f'SELECT COUNT(*) {base_from}{where_clauses}', params
    ).fetchone()[0]

    fields = '''q.id, q.content, q.answer, q.subject, q.type, q.difficulty,
               q.avg_cost, q.source, q.created_at, q.content_type, q.image_path, q.answer_image_path,
               COALESCE(s.times_correct, 0) as times_correct,
               COALESCE(s.times_wrong, 0) as times_wrong,
               COALESCE(s.accuracy, 0) as accuracy,
               s.last_review'''
    main_query = f'SELECT {fields} {base_from}{where_clauses}'

    if random_order:
        main_query += ' ORDER BY RANDOM()'
    else:
        main_query += ' ORDER BY q.id DESC'

    if limit > 0:
        main_query += ' LIMIT ?'
        params.append(limit)
    if offset > 0:
        main_query += ' OFFSET ?'
        params.append(offset)

    rows = db.execute(main_query, params).fetchall()

    questions = []
    for row in rows:
        q = _row_to_question(row)
        q.update({
            'has_state': row['last_review'] is not None,
            'times_correct': row['times_correct'],
            'times_wrong': row['times_wrong'],
            'accuracy': row['accuracy'],
        })
        questions.append(q)

    return jsonify({'questions': questions, 'total': total})


@manage_bp.route('/api/questions/<int:question_id>', methods=['GET'])
def get_question(question_id):
    db = get_db()
    row = db.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
    if not row:
        return jsonify({'error': '题目不存在'}), 404

    state_row = db.execute(
        'SELECT * FROM user_question_state WHERE question_id = ?', (question_id,)
    ).fetchone()

    question = _row_to_question(row)
    question['state'] = None

    if state_row:
        question['state'] = {
            'lambda_': state_row['lambda_'],
            'last_review': state_row['last_review'],
            'accuracy': state_row['accuracy'],
            'times_correct': state_row['times_correct'],
            'times_wrong': state_row['times_wrong'],
        }

    return jsonify({'question': question})


@manage_bp.route('/api/questions', methods=['POST'])
def create_question():
    db = get_db()
    data = request.get_json(force=True) or {}

    content = data.get('content', '').strip()
    answer = data.get('answer', '').strip()

    if not content:
        return jsonify({'error': '题目内容不能为空'}), 400
    if not answer:
        return jsonify({'error': '答案不能为空'}), 400

    subject = data.get('subject', '').strip()
    qtype = data.get('type', '').strip()
    difficulty = max(0.0, min(1.0, float(data.get('difficulty', 0.5))))
    avg_cost = max(1.0, min(60.0, float(data.get('avg_cost', 5.0))))
    source = data.get('source', '').strip()

    cursor = db.execute('''
        INSERT INTO questions (content, answer, subject, type, difficulty, avg_cost, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (content, answer, subject, qtype, difficulty, avg_cost, source))
    db.commit()

    return jsonify({'id': cursor.lastrowid, 'message': '题目已创建'})


@manage_bp.route('/api/questions/<int:question_id>', methods=['PUT'])
def update_question(question_id):
    db = get_db()
    row = db.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
    if not row:
        return jsonify({'error': '题目不存在'}), 404

    data = request.get_json(force=True) or {}

    content = data.get('content', row['content']).strip()
    answer = data.get('answer', row['answer']).strip()
    subject = data.get('subject', row['subject']).strip()
    qtype = data.get('type', row['type']).strip()
    difficulty = max(0.0, min(1.0, float(data.get('difficulty', row['difficulty']))))
    avg_cost = max(1.0, min(60.0, float(data.get('avg_cost', row['avg_cost']))))
    source = data.get('source', row['source']).strip()

    if not content:
        return jsonify({'error': '题目内容不能为空'}), 400

    db.execute('''
        UPDATE questions SET content=?, answer=?, subject=?, type=?, difficulty=?, avg_cost=?, source=?
        WHERE id=?
    ''', (content, answer, subject, qtype, difficulty, avg_cost, source, question_id))
    db.commit()

    return jsonify({'message': '题目已更新'})


@manage_bp.route('/api/questions/<int:question_id>', methods=['DELETE'])
def delete_question(question_id):
    db = get_db()
    row = db.execute('SELECT * FROM questions WHERE id = ?', (question_id,)).fetchone()
    if not row:
        return jsonify({'error': '题目不存在'}), 404

    if row['image_path']:
        img_path = os.path.join(UPLOADS_FOLDER, row['image_path'])
        if os.path.exists(img_path):
            os.remove(img_path)

    db.execute('DELETE FROM questions WHERE id = ?', (question_id,))
    db.commit()
    return jsonify({'message': '题目已删除'})


# ----------------------------------------------------------------
# Unattributed questions pool
# ----------------------------------------------------------------

@manage_bp.route('/api/questions/unattributed', methods=['GET'])
def list_unattributed_questions():
    """列出未关联任何知识节点的题目（未归属池）"""
    db = get_db()
    subject = request.args.get('subject', '').strip()
    qtype = request.args.get('type', '').strip()
    search = request.args.get('search', '').strip()

    where = ["q.id NOT IN (SELECT DISTINCT question_id FROM question_node_mapping)"]
    params = []

    if subject:
        where.append("q.subject = ?")
        params.append(subject)
    if qtype:
        where.append("q.type = ?")
        params.append(qtype)
    if search:
        where.append("q.content LIKE ?")
        params.append(f'%{search}%')

    clause = ' AND '.join(where)
    rows = db.execute(
        f'SELECT q.* FROM questions q WHERE {clause} ORDER BY q.created_at DESC', params
    ).fetchall()

    questions = []
    for row in rows:
        q = {
            'id': row['id'],
            'content': row['content'],
            'answer': row['answer'],
            'subject': row['subject'] or '',
            'type': row['type'] or '',
            'difficulty': row['difficulty'],
            'avg_cost': row['avg_cost'],
            'source': row['source'] or '',
            'created_at': row['created_at'],
            'content_type': row['content_type'],
        }
        if q['content_type'] == 'image' and row['image_path']:
            q['image_url'] = f'/practice/uploads/{row["image_path"]}'
        else:
            q['image_url'] = None
        if row['answer_image_path']:
            q['answer_image_url'] = f'/practice/uploads/{row["answer_image_path"]}'
        else:
            q['answer_image_url'] = None
        questions.append(q)

    nodes = db.execute('SELECT id, name, subject FROM knowledge_nodes ORDER BY subject, name').fetchall()
    knowledge_nodes = [{'id': n['id'], 'name': n['name'], 'subject': n['subject']} for n in nodes]

    return jsonify({
        'questions': questions,
        'knowledge_nodes': knowledge_nodes,
        'total': len(questions),
    })


@manage_bp.route('/api/questions/<int:question_id>/parameters', methods=['PUT'])
def update_question_parameters(question_id):
    """仅更新算法相关参数（subject, type, difficulty, avg_cost）+ 知识节点关联"""
    db = get_db()
    row = db.execute('SELECT id FROM questions WHERE id = ?', (question_id,)).fetchone()
    if not row:
        return jsonify({'error': '题目不存在'}), 404

    data = request.get_json(silent=True) or {}
    subject = data.get('subject', '').strip()
    qtype = data.get('type', '').strip()

    difficulty = data.get('difficulty')
    if difficulty is not None:
        difficulty = max(0.0, min(1.0, float(difficulty)))
    else:
        difficulty = None

    avg_cost = data.get('avg_cost')
    if avg_cost is not None:
        avg_cost = max(1.0, min(60.0, float(avg_cost)))
    else:
        avg_cost = None

    fields = []
    values = []
    if subject is not None:
        fields.append('subject=?')
        values.append(subject)
    if qtype is not None:
        fields.append('type=?')
        values.append(qtype)
    if difficulty is not None:
        fields.append('difficulty=?')
        values.append(difficulty)
    if avg_cost is not None:
        fields.append('avg_cost=?')
        values.append(avg_cost)

    if fields:
        values.append(question_id)
        db.execute(f'UPDATE questions SET {", ".join(fields)} WHERE id=?', values)

    node_ids = data.get('knowledge_node_ids')
    if node_ids is not None:
        db.execute('DELETE FROM question_node_mapping WHERE question_id=?', (question_id,))
        if node_ids:
            for nid in node_ids:
                try:
                    db.execute(
                        'INSERT OR IGNORE INTO question_node_mapping (question_id, node_id) VALUES (?, ?)',
                        (question_id, int(nid))
                    )
                except Exception:
                    pass

    db.commit()
    return jsonify({'message': '参数已更新'})


# ----------------------------------------------------------------
# PDF upload (text extraction)
# ----------------------------------------------------------------

@manage_bp.route('/api/pdf/upload', methods=['POST'])
def upload_pdf():
    if 'file' not in request.files:
        return jsonify({'error': '请选择文件'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '请选择文件'}), 400

    try:
        try:
            from PyPDF2 import PdfReader
            import io
            reader = PdfReader(io.BytesIO(file.read()))
            text_parts = [page.extract_text() for page in reader.pages if page.extract_text()]
            text = '\n\n'.join(text_parts)
        except ImportError:
            text = file.read().decode('utf-8', errors='replace')

        if not text.strip():
            return jsonify({
                'message': 'PDF 中未提取到文字。可能是图片型 PDF，需要 OCR 支持。',
                'text_preview': '',
                'char_count': 0
            })

        return jsonify({
            'message': '上传成功',
            'text_preview': text[:5000],
            'char_count': len(text),
            'full_text': text,
        })
    except Exception as e:
        return jsonify({'error': f'PDF 解析失败: {str(e)}'}), 400
