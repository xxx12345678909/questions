"""
Recommendation orchestrator — composes pure computation modules with repository data.
The "commander" of the adaptive recommendation pipeline: fetches clean snapshots
from the Repository layer, applies multi-dimensional damping coefficients, and
executes the 3-pool interleaving pipeline.
"""
import random as _random
from datetime import datetime, UTC

from practice import SUBJECT_WEIGHTS
from practice.core.ebbinghaus import calc_score
from practice.graph.damping import calc_prerequisite_damping
from practice.adaptive.fatigue import calc_fatigue_adjusted_score
from practice.adaptive.irt import calc_difficulty_damping
from practice.core.irt import calc_irt_difficulty_damping
from practice.repository.knowledge_repo import get_prerequisite_retentions, get_node_sliding_accuracy, fetch_all_nodes_irt_theta
from practice.repository.question_repo import fetch_all_questions_with_state, fetch_question_node_ids


def build_recommendation(db, budget, review_ratio, wrong_ratio, new_ratio,
                          threshold, max_consecutive, shuffle_within=False,
                          enable_knowledge_graph=True,
                          fatigue=None, enable_difficulty_adaptation=False,
                          enable_irt=False):
    """
    Generate today's recommended question list, fusing knowledge graph prerequisite
    damping, fatigue down-weighting, and dynamic difficulty adaptation.

    Pools:
      - review: previously reviewed, retention < threshold
      - wrong:  answered incorrectly before (times_wrong > 0)
      - new:    never reviewed

    Args:
        db: SQLite database connection
        budget: total number of questions to recommend
        review_ratio: proportion of review pool (e.g. 0.6)
        wrong_ratio: proportion of wrong pool (e.g. 0.2)
        new_ratio: proportion of new pool (e.g. 0.2)
        threshold: retention threshold (e.g. 0.6)
        max_consecutive: max consecutive questions of same type
        shuffle_within: whether to shuffle within pools
        enable_knowledge_graph: enable prerequisite dependency damping
        fatigue: current session fatigue factor F, None to skip
        enable_difficulty_adaptation: enable Gaussian difficulty matching (legacy)
        enable_irt: enable IRT 3PL-based difficulty damping (v5, overrides DDA)

    Returns:
        dict: {questions, total, breakdown: {review, wrong, new}}
    """
    now = datetime.now(UTC).replace(tzinfo=None)

    # Pre-compute sliding window accuracy per knowledge node for DDA (legacy)
    node_theta_cache = {}
    if enable_difficulty_adaptation and not enable_irt:
        nodes = db.execute('SELECT id FROM knowledge_nodes').fetchall()
        for n in nodes:
            node_theta_cache[n['id']] = get_node_sliding_accuracy(db, n['id'])

    # Pre-fetch IRT theta values for all knowledge nodes (v5)
    node_irt_theta = {}
    if enable_irt:
        node_irt_theta = fetch_all_nodes_irt_theta(db)

    rows = fetch_all_questions_with_state(db)

    review_pool, wrong_pool, new_pool = [], [], []

    for row in rows:
        qid = row['id']
        subject = row['subject'] or ''
        avg_cost = row['avg_cost']
        difficulty = row['difficulty']
        lambda_ = row['lambda_'] if row['lambda_'] is not None else 0.3
        last_review = row['last_review']
        times_wrong = row['times_wrong'] if row['times_wrong'] is not None else 0

        # 1D: base forgetting curve score
        score, priority, retention = calc_score(
            lambda_, last_review, subject, times_wrong, avg_cost, now
        )

        # 2D: cascade prerequisite knowledge graph damping
        damping = 1.0
        if enable_knowledge_graph:
            prereq_retentions = get_prerequisite_retentions(db, qid, now)
            damping = calc_prerequisite_damping(prereq_retentions, retention_threshold=threshold)
            score = score * damping

        # 3D: cascade difficulty-ability matching damping
        difficulty_damping = 1.0
        if enable_irt:
            # v5: IRT 3PL-based ZPD damping (replaces heuristic Gaussian)
            irt_b = row['irt_b'] if row['irt_b'] is not None else 0.0
            node_ids = fetch_question_node_ids(db, qid)
            if node_ids:
                thetas = [node_irt_theta.get(nid, 0.0) for nid in node_ids]
                avg_theta = sum(thetas) / len(thetas)
                difficulty_damping = calc_irt_difficulty_damping(avg_theta, irt_b)
            else:
                difficulty_damping = calc_irt_difficulty_damping(0.0, irt_b)
            score = score * difficulty_damping
        elif enable_difficulty_adaptation and node_theta_cache:
            node_ids = fetch_question_node_ids(db, qid)
            if node_ids:
                thetas = [node_theta_cache.get(nid, 0.5) for nid in node_ids]
                avg_theta = sum(thetas) / len(thetas)
                difficulty_damping = calc_difficulty_damping(difficulty, avg_theta)
                score = score * difficulty_damping

        # 4D: physiological fatigue step-down weighting
        if fatigue is not None and fatigue > 0:
            score = calc_fatigue_adjusted_score(score, fatigue, difficulty)

        content_type = row['content_type'] if 'content_type' in row.keys() else 'text'
        image_path = row['image_path'] if 'image_path' in row.keys() else ''
        answer_image_path = row['answer_image_path'] if 'answer_image_path' in row.keys() else ''

        item = {
            'id': qid,
            'content': row['content'],
            'answer': row['answer'],
            'subject': subject,
            'type': row['type'] or '',
            'difficulty': difficulty,
            'avg_cost': avg_cost,
            'source': row['source'] or '',
            'content_type': content_type,
            'image_url': f'/practice/uploads/{image_path}' if content_type == 'image' and image_path else '',
            'answer_image_url': f'/practice/uploads/{answer_image_path}' if answer_image_path else '',
            'lambda_': lambda_,
            'retention': round(retention, 4),
            'priority': round(priority, 4),
            'score': round(score, 4),
            'damping': round(damping, 4) if enable_knowledge_graph else 1.0,
            'difficulty_damping': round(difficulty_damping, 4),
            'fatigue_adjusted': fatigue is not None and fatigue > 0,
        }

        if row['last_review'] is None:
            item['pool'] = 'new'
            new_pool.append(item)
        elif times_wrong > 0 and row['times_correct'] is not None:
            item['pool'] = 'wrong'
            wrong_pool.append(item)
        elif retention < threshold:
            item['pool'] = 'review'
            review_pool.append(item)
        else:
            continue

    # Sort by score descending
    review_pool.sort(key=lambda x: x['score'], reverse=True)
    wrong_pool.sort(key=lambda x: x['score'], reverse=True)
    new_pool.sort(key=lambda x: x['score'], reverse=True)

    if shuffle_within:
        _random.shuffle(review_pool)
        _random.shuffle(wrong_pool)
        _random.shuffle(new_pool)

    review_slots = round(budget * review_ratio)
    wrong_slots = round(budget * wrong_ratio)
    new_slots = budget - review_slots - wrong_slots

    pools = [
        ('review', review_pool, review_slots),
        ('wrong', wrong_pool, wrong_slots),
        ('new', new_pool, new_slots),
    ]

    # Interleaving with consecutive-type constraint
    result = []
    recent_types = []
    pool_indices = {'review': 0, 'wrong': 0, 'new': 0}
    pool_counts = {'review': 0, 'wrong': 0, 'new': 0}
    pool_order = ['review', 'wrong', 'new']

    while len(result) < budget:
        added = False
        for pool_name in pool_order:
            pool_data = next(p for p in pools if p[0] == pool_name)
            pool_list = pool_data[1]
            max_count = pool_data[2]
            idx = pool_indices[pool_name]

            if pool_counts[pool_name] >= max_count:
                continue

            found = None
            search_idx = idx
            while search_idx < len(pool_list):
                candidate = pool_list[search_idx]
                ctype = candidate['type']
                if ctype not in recent_types[-max_consecutive:] or not recent_types:
                    found = candidate
                    pool_indices[pool_name] = search_idx + 1
                    break
                search_idx += 1

            if found is not None:
                result.append(found)
                pool_counts[pool_name] += 1
                recent_types.append(found['type'])
                if len(recent_types) > max_consecutive:
                    recent_types.pop(0)
                added = True

        if not added:
            for pool_name in pool_order:
                pool_list = next(p for p in pools if p[0] == pool_name)[1]
                max_count = next(p for p in pools if p[0] == pool_name)[2]
                idx = pool_indices[pool_name]
                if pool_counts[pool_name] < max_count and idx < len(pool_list):
                    result.append(pool_list[idx])
                    pool_indices[pool_name] = idx + 1
                    pool_counts[pool_name] += 1
                    added = True
                    break

        if not added:
            break

    return {
        'questions': result,
        'total': len(result),
        'breakdown': {
            'review': pool_counts['review'],
            'wrong': pool_counts['wrong'],
            'new': pool_counts['new'],
        },
    }
