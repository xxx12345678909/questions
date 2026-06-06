"""Knowledge graph routes — topology, dependencies, path planning, mastery heatmap."""
import json

from flask import Blueprint, request, jsonify

from practice.db import get_db, update_node_mastery
from practice.engine import recommend_learning_path
from practice.repository.cache_proxy import cache_service

graph_bp = Blueprint('graph_api', __name__)


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _mastery_color(mastery):
    """Map mastery score to red-yellow-green gradient."""
    if mastery < 0.3:
        return '#ef4444'
    elif mastery < 0.5:
        return '#f97316'
    elif mastery < 0.7:
        return '#eab308'
    elif mastery < 0.85:
        return '#84cc16'
    else:
        return '#22c55e'


# ================================================================
# Knowledge Nodes CRUD
# ================================================================

@graph_bp.route('/api/knowledge-nodes', methods=['GET', 'POST'])
def knowledge_nodes_api():
    """获取或创建知识点."""
    db = get_db()

    if request.method == 'GET':
        subject = request.args.get('subject', '')
        nodes = db.execute('''
            SELECT id, name, subject, ideal_retention, created_at
            FROM knowledge_nodes
            WHERE subject = ? OR ? = ''
            ORDER BY created_at DESC
        ''', (subject, subject)).fetchall()

        return jsonify({
            'nodes': [dict(n) for n in nodes],
            'total': len(nodes)
        })

    # POST: 创建新知识点
    data = request.get_json(force=True) or {}
    name = data.get('name', '').strip()
    subject = data.get('subject', '').strip()
    ideal_retention = float(data.get('ideal_retention', 0.8))

    if not name or not subject:
        return jsonify({'error': '知识点名称和所属科目不能为空'}), 400

    if ideal_retention < 0.1 or ideal_retention > 1.0:
        return jsonify({'error': '理想保留率应在 0.1-1.0 之间'}), 400

    try:
        cursor = db.execute('''
            INSERT INTO knowledge_nodes (name, subject, ideal_retention)
            VALUES (?, ?, ?)
        ''', (name, subject, ideal_retention))
        db.commit()

        return jsonify({
            'id': cursor.lastrowid,
            'name': name,
            'subject': subject,
            'ideal_retention': ideal_retention,
            'message': '知识点已创建'
        })
    except Exception:
        return jsonify({'error': f'知识点 "{name}" 已存在'}), 400


@graph_bp.route('/api/knowledge-nodes/<int:node_id>', methods=['GET', 'PUT', 'DELETE'])
def knowledge_node_detail(node_id):
    """获取、更新或删除知识点."""
    db = get_db()

    node = db.execute(
        'SELECT id, name, subject, ideal_retention, created_at FROM knowledge_nodes WHERE id = ?',
        (node_id,)
    ).fetchone()

    if not node:
        return jsonify({'error': '知识点不存在'}), 404

    if request.method == 'GET':
        return jsonify(dict(node))

    if request.method == 'PUT':
        data = request.get_json(force=True) or {}
        name = data.get('name', node['name']).strip()
        subject = data.get('subject', node['subject']).strip()
        ideal_retention = float(data.get('ideal_retention', node['ideal_retention']))

        if not name or not subject:
            return jsonify({'error': '知识点名称和所属科目不能为空'}), 400

        if ideal_retention < 0.1 or ideal_retention > 1.0:
            return jsonify({'error': '理想保留率应在 0.1-1.0 之间'}), 400

        db.execute('''
            UPDATE knowledge_nodes
            SET name = ?, subject = ?, ideal_retention = ?
            WHERE id = ?
        ''', (name, subject, ideal_retention, node_id))
        db.commit()

        return jsonify({
            'id': node_id,
            'name': name,
            'subject': subject,
            'ideal_retention': ideal_retention,
            'message': '知识点已更新'
        })

    if request.method == 'DELETE':
        db.execute('DELETE FROM knowledge_nodes WHERE id = ?', (node_id,))
        db.commit()
        return jsonify({'message': '知识点已删除'})


# ================================================================
# Knowledge Dependencies
# ================================================================

@graph_bp.route('/api/knowledge-dependencies', methods=['GET', 'POST'])
def knowledge_dependencies_api():
    """获取或创建知识点依赖关系."""
    db = get_db()

    if request.method == 'GET':
        node_id = request.args.get('node_id', type=int)
        if node_id:
            deps = db.execute('''
                SELECT kd.node_id, kd.prerequisite_node_id,
                       n1.name as node_name, n2.name as prereq_name
                FROM knowledge_dependency kd
                JOIN knowledge_nodes n1 ON kd.node_id = n1.id
                JOIN knowledge_nodes n2 ON kd.prerequisite_node_id = n2.id
                WHERE kd.node_id = ?
            ''', (node_id,)).fetchall()
        else:
            deps = db.execute('''
                SELECT kd.node_id, kd.prerequisite_node_id,
                       n1.name as node_name, n2.name as prereq_name
                FROM knowledge_dependency kd
                JOIN knowledge_nodes n1 ON kd.node_id = n1.id
                JOIN knowledge_nodes n2 ON kd.prerequisite_node_id = n2.id
            ''').fetchall()

        return jsonify({'dependencies': [dict(d) for d in deps], 'total': len(deps)})

    # POST: 创建依赖关系
    data = request.get_json(force=True) or {}
    node_id = int(data.get('node_id', 0))
    prereq_id = int(data.get('prerequisite_node_id', 0))

    if not node_id or not prereq_id:
        return jsonify({'error': '节点ID和前置节点ID不能为空'}), 400

    if node_id == prereq_id:
        return jsonify({'error': '节点不能依赖于自身'}), 400

    try:
        db.execute('''
            INSERT INTO knowledge_dependency (node_id, prerequisite_node_id)
            VALUES (?, ?)
        ''', (node_id, prereq_id))
        db.commit()

        return jsonify({
            'node_id': node_id,
            'prerequisite_node_id': prereq_id,
            'message': '依赖关系已创建'
        })
    except Exception:
        return jsonify({'error': '该依赖关系已存在'}), 400


@graph_bp.route('/api/knowledge-dependencies/<int:node_id>/<int:prereq_id>', methods=['DELETE'])
def delete_dependency(node_id, prereq_id):
    """删除知识点依赖关系."""
    db = get_db()
    db.execute('''
        DELETE FROM knowledge_dependency
        WHERE node_id = ? AND prerequisite_node_id = ?
    ''', (node_id, prereq_id))
    db.commit()
    return jsonify({'message': '依赖关系已删除'})


# ================================================================
# Question-Knowledge Node Association
# ================================================================

@graph_bp.route('/api/questions/<int:question_id>/knowledge-nodes', methods=['GET', 'POST', 'DELETE'])
def question_knowledge_nodes(question_id):
    """获取或管理题目关联的知识点."""
    db = get_db()

    question = db.execute('SELECT id FROM questions WHERE id = ?', (question_id,)).fetchone()
    if not question:
        return jsonify({'error': '题目不存在'}), 404

    if request.method == 'GET':
        nodes = db.execute('''
            SELECT n.id, n.name, n.subject, n.ideal_retention
            FROM knowledge_nodes n
            JOIN question_node_mapping m ON n.id = m.node_id
            WHERE m.question_id = ?
        ''', (question_id,)).fetchall()

        return jsonify({'nodes': [dict(n) for n in nodes], 'total': len(nodes)})

    data = request.get_json(force=True) or {}
    node_id = int(data.get('node_id', 0))

    if not node_id:
        return jsonify({'error': '知识点ID不能为空'}), 400

    node = db.execute('SELECT id FROM knowledge_nodes WHERE id = ?', (node_id,)).fetchone()
    if not node:
        return jsonify({'error': '知识点不存在'}), 404

    if request.method == 'POST':
        try:
            db.execute('''
                INSERT INTO question_node_mapping (question_id, node_id)
                VALUES (?, ?)
            ''', (question_id, node_id))
            db.commit()
            return jsonify({'message': '知识点已关联'})
        except Exception:
            return jsonify({'error': '该知识点已关联到此题目'}), 400

    if request.method == 'DELETE':
        db.execute('''
            DELETE FROM question_node_mapping
            WHERE question_id = ? AND node_id = ?
        ''', (question_id, node_id))
        db.commit()
        return jsonify({'message': '知识点关联已移除'})


# ================================================================
# Graph Topology & Visualization
# ================================================================

@graph_bp.route('/api/graph/topology', methods=['GET'])
def graph_topology():
    """返回知识图谱全量拓扑数据，兼容 ECharts graph 格式。"""
    db = get_db()

    nodes = db.execute('''
        SELECT id, name, subject,
               COALESCE(current_mastery, 0.0) as mastery,
               COALESCE(rolling_accuracy, 0.0) as accuracy
        FROM knowledge_nodes
        ORDER BY subject, id
    ''').fetchall()

    node_counts = {}
    counts = db.execute('''
        SELECT node_id, COUNT(*) as cnt
        FROM question_node_mapping
        GROUP BY node_id
    ''').fetchall()
    for c in counts:
        node_counts[c['node_id']] = c['cnt']

    edges = db.execute('''
        SELECT kd.node_id, kd.prerequisite_node_id
        FROM knowledge_dependency kd
    ''').fetchall()

    node_list = []
    for n in nodes:
        m = n['mastery'] or 0.0
        node_list.append({
            'id': str(n['id']),
            'name': n['name'],
            'label': n['name'],
            'subject': n['subject'],
            'category': n['subject'],
            'symbolSize': max(25, min(60, 25 + m * 30)),
            'mastery': round(m, 4),
            'accuracy': round(n['accuracy'] or 0.0, 4),
            'itemStyle': {'color': _mastery_color(m)},
            'value': n['name'],
        })

    edge_list = [{
        'source': str(e['node_id']),
        'target': str(e['prerequisite_node_id']),
    } for e in edges]

    categories = []
    seen_subjects = set()
    subject_colors = {
        '高数': '#ef4444', '线代': '#f97316', '408': '#3b82f6',
        '英语': '#22c55e', '概率': '#a855f7', '政治': '#eab308',
        '算法': '#06b6d4', '数学': '#ec4899',
    }
    for n in nodes:
        subj = n['subject']
        if subj not in seen_subjects:
            seen_subjects.add(subj)
            categories.append({
                'name': subj,
                'itemStyle': {'color': subject_colors.get(subj, '#6366f1')},
            })

    return jsonify({
        'status': 'success',
        'nodes': node_list,
        'edges': edge_list,
        'categories': categories,
    })


@graph_bp.route('/api/graph/edge/update', methods=['POST'])
def graph_edge_update():
    """动态编辑依赖关系（添加或删除边）。"""
    db = get_db()
    data = request.get_json(force=True) or {}

    source_node_id = int(data.get('source_node_id', 0))
    target_node_id = int(data.get('target_node_id', 0))
    action = data.get('action', 'add')

    if not source_node_id or not target_node_id:
        return jsonify({'error': '源节点和目标节点不能为空'}), 400
    if source_node_id == target_node_id:
        return jsonify({'error': '节点不能依赖于自身'}), 400

    for nid in [source_node_id, target_node_id]:
        node = db.execute('SELECT id FROM knowledge_nodes WHERE id = ?', (nid,)).fetchone()
        if not node:
            return jsonify({'error': f'知识点 #{nid} 不存在'}), 404

    if action == 'add':
        # --- Tarjan cycle detection: simulate the new edge and check for SCC ---
        try:
            edges_rows = db.execute(
                'SELECT node_id, prerequisite_node_id FROM knowledge_dependency'
            ).fetchall()
            temp_adj_matrix = {}
            for r in edges_rows:
                temp_adj_matrix.setdefault(r['node_id'], []).append(r['prerequisite_node_id'])
            temp_adj_matrix.setdefault(source_node_id, []).append(target_node_id)

            from practice.graph.reducer import verify_graph_cycle_tarjan
            if verify_graph_cycle_tarjan(len(temp_adj_matrix), temp_adj_matrix):
                return jsonify({
                    'error': '硬熔断警告：所选依赖关联会导致知识图谱产生双向循环依赖死锁！'
                }), 400
        except Exception as e:
            return jsonify({'error': f'环路校验失败: {str(e)}'}), 400

        try:
            db.execute(
                'INSERT INTO knowledge_dependency (node_id, prerequisite_node_id) VALUES (?, ?)',
                (source_node_id, target_node_id)
            )
            db.commit()
            return jsonify({'message': '依赖关系已添加，拓扑结构通过收敛安全校验。', 'action': 'add'})
        except Exception as e:
            return jsonify({'error': f'添加失败: {str(e)}'}), 400

    elif action == 'remove':
        db.execute(
            'DELETE FROM knowledge_dependency WHERE node_id = ? AND prerequisite_node_id = ?',
            (source_node_id, target_node_id)
        )
        db.commit()
        return jsonify({'message': '依赖关系已删除', 'action': 'remove'})

    return jsonify({'error': '无效的操作，请使用 add 或 remove'}), 400


# ================================================================
# Learning Path Recommendation
# ================================================================

@graph_bp.route('/api/path/recommend', methods=['GET'])
def path_recommend():
    """获取专项通关最短学习路径（cache-aside 缓冲）。"""
    db = get_db()
    target_node_id = request.args.get('target_node_id', type=int)
    if not target_node_id:
        return jsonify({'error': '请指定目标知识点 target_node_id'}), 400

    threshold = request.args.get('mastery_threshold', type=float, default=0.7)
    cache_key = f"practice:graph:learning_path:{target_node_id}"

    # 1. Cache hit — return in-memory snapshot
    cached = cache_service.get(cache_key)
    if cached:
        result = json.loads(cached)
        result['status'] = 'success'
        result['cached'] = True
        return jsonify(result)

    # 2. Cache miss — compute, store, return
    result = recommend_learning_path(db, target_node_id, mastery_threshold=threshold)

    if 'error' in result:
        return jsonify(result), 404

    cache_service.set(cache_key, json.dumps(result), ttl=600)
    result['status'] = 'success'
    return jsonify(result)


# ================================================================
# Mastery Heatmap
# ================================================================

@graph_bp.route('/api/mastery/heatmap', methods=['GET'])
def mastery_heatmap():
    """获取知识点掌握度热力图数据。"""
    db = get_db()
    nodes = db.execute('''
        SELECT id, name, subject,
               COALESCE(current_mastery, 0.0) as mastery,
               COALESCE(rolling_accuracy, 0.0) as accuracy
        FROM knowledge_nodes
        ORDER BY subject, id
    ''').fetchall()

    if not nodes:
        return jsonify({'nodes': [], 'subjects': []})

    subjects_order = []
    seen = set()
    heatmap_data = []
    for n in nodes:
        subj = n['subject']
        if subj not in seen:
            seen.add(subj)
            subjects_order.append(subj)
        heatmap_data.append({
            'id': n['id'],
            'name': n['name'],
            'subject': subj,
            'mastery': round(n['mastery'] or 0.0, 4),
            'accuracy': round(n['accuracy'] or 0.0, 4),
            'color': _mastery_color(n['mastery'] or 0.0),
        })

    return jsonify({
        'nodes': heatmap_data,
        'subjects': subjects_order,
        'total': len(heatmap_data),
    })
