"""
Recommendation orchestrator — composes pure computation modules with repository data.
The "commander" of the adaptive recommendation pipeline: fetches clean snapshots
from the Repository layer, applies multi-dimensional damping coefficients, and
executes the 3-pool interleaving pipeline.
"""
import random as _random
from datetime import datetime, UTC

from practice import SUBJECT_WEIGHTS
from practice.adaptive.unified import unified_score
from practice.repository.knowledge_repo import (
    batch_get_prerequisite_retentions, fetch_all_nodes_irt_theta,
)
from practice.repository.question_repo import fetch_all_questions_with_state


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

    [Complexity] Time: O(N log N + M + D + S) — N = questions (sort dominates),
                        M/D/S = batch preload row counts (3 upfront queries + in-memory
                        computation replace N x nested SQL calls).
                Space: O(N) — three pools hold all scored items

    TODO[perf]: The interleaving linear scan was O(B * pool_size). Now pre-partitions
                each pool by type into sub-buckets — O(B * T) where T = distinct
                types per pool (typically 2-5, far smaller than pool_size).
    """
    now = datetime.now(UTC).replace(tzinfo=None)

    # Pre-fetch IRT theta values for all knowledge nodes
    node_irt_theta = {}
    if enable_irt:
        node_irt_theta = fetch_all_nodes_irt_theta(db)

    rows = fetch_all_questions_with_state(db)

    # ---- Batch preload: question→node mapping (2 per-question SQLs → 1 upfront) ----
    all_qids = [r['id'] for r in rows]
    q_to_nodes = {}  # question_id → list of node_ids
    if all_qids:
        placeholders = ','.join('?' * len(all_qids))
        qnm_rows = db.execute(
            f'SELECT question_id, node_id FROM question_node_mapping WHERE question_id IN ({placeholders})',
            tuple(all_qids)
        ).fetchall()
        for qr in qnm_rows:
            q_to_nodes.setdefault(qr['question_id'], []).append(qr['node_id'])

    # ---- Batch preload: prerequisite retentions (N x nested SQL → 3 upfront) ----
    prereq_retention_cache = {}
    if enable_knowledge_graph:
        prereq_retention_cache = batch_get_prerequisite_retentions(db, all_qids, now)

    review_pool, wrong_pool, new_pool = [], [], []

    for row in rows:
        qid = row['id']
        subject = row['subject'] or ''
        avg_cost = row['avg_cost']
        difficulty = row['difficulty']
        lambda_ = row['lambda_'] if row['lambda_'] is not None else 0.3
        last_review = row['last_review']
        times_wrong = row['times_wrong'] if row['times_wrong'] is not None else 0
        subject_weight = SUBJECT_WEIGHTS.get(subject, 1.0)

        # Compute seconds since last review
        seconds_since_review = None
        if last_review is not None:
            if isinstance(last_review, str):
                from datetime import datetime as _dt
                last_review = _dt.fromisoformat(last_review)
            seconds_since_review = (now - last_review).total_seconds()

        # Graph prerequisite retentions
        prereq_retentions = []
        if enable_knowledge_graph:
            prereq_retentions = prereq_retention_cache.get(qid, [])

        # IRT theta (pre-loaded)
        irt_b = row['irt_b'] if row['irt_b'] is not None else 0.0
        node_ids = q_to_nodes.get(qid, [])
        avg_theta = 0.0
        if enable_irt and node_ids:
            thetas = [node_irt_theta.get(nid, 0.0) for nid in node_ids]
            avg_theta = sum(thetas) / len(thetas)

        # --- Unified scoring: all 4 dimensions in one call ---
        score, priority, retention, damping, difficulty_damping, _fatigue_factor = unified_score(
            theta_irt=avg_theta if enable_irt else None,
            lambda_=lambda_,
            seconds_since_review=seconds_since_review,
            subject_weight=subject_weight,
            irt_b=irt_b if enable_irt else None,
            prereq_retentions=prereq_retentions if enable_knowledge_graph else [],
            avg_cost=avg_cost,
            times_wrong=times_wrong,
            fatigue=fatigue or 0.0,
            difficulty=difficulty,
            retention_threshold=threshold,
        )

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

    # Pre-partition each pool by type into sub-buckets (O(N) once, then O(1) lookup)
    pool_type_buckets = {}
    for pool_name, pool_list, _max_count in pools:
        buckets = {}
        for item in pool_list:
            ctype = item['type'] or ''
            buckets.setdefault(ctype, []).append(item)
        pool_type_buckets[pool_name] = buckets

    # Interleaving with consecutive-type constraint
    result = []
    recent_types = []
    pool_counts = {'review': 0, 'wrong': 0, 'new': 0}
    pool_limits = {'review': review_slots, 'wrong': wrong_slots, 'new': new_slots}
    # Per-pool per-type cursor into the bucket
    bucket_cursors = {
        pn: {ct: 0 for ct in pool_type_buckets[pn]}
        for pn in ['review', 'wrong', 'new']
    }
    pool_order = ['review', 'wrong', 'new']

    while len(result) < budget:
        added = False
        for pool_name in pool_order:
            if pool_counts[pool_name] >= pool_limits[pool_name]:
                continue

            buckets = pool_type_buckets[pool_name]
            # Find the highest-scored candidate whose type is allowed
            best_candidate = None
            best_ctype = None
            for ctype, bucket in buckets.items():
                if ctype in recent_types[-max_consecutive:] and recent_types:
                    continue
                cursor = bucket_cursors[pool_name].get(ctype, 0)
                if cursor < len(bucket):
                    candidate = bucket[cursor]
                    if best_candidate is None or candidate['score'] > best_candidate['score']:
                        best_candidate = candidate
                        best_ctype = ctype

            if best_candidate is not None:
                result.append(best_candidate)
                bucket_cursors[pool_name][best_ctype] += 1
                pool_counts[pool_name] += 1
                recent_types.append(best_ctype)
                if len(recent_types) > max_consecutive:
                    recent_types.pop(0)
                added = True

        if not added:
            # Fallback: pick the next available item from any pool (ignore type constraint)
            for pool_name in pool_order:
                if pool_counts[pool_name] >= pool_limits[pool_name]:
                    continue
                buckets = pool_type_buckets[pool_name]
                for ctype, bucket in buckets.items():
                    cursor = bucket_cursors[pool_name].get(ctype, 0)
                    if cursor < len(bucket):
                        result.append(bucket[cursor])
                        bucket_cursors[pool_name][ctype] = cursor + 1
                        pool_counts[pool_name] += 1
                        added = True
                        break
                if added:
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
