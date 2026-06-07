"""
Critical Path Method (CPM) for study planning — pure graph algorithm.

Models the knowledge dependency DAG as an AOE (Activity on Edge) network:
  - Nodes = knowledge points (events: "learned this topic")
  - Edges = prerequisite dependencies (activity: "learn target after prereq")
  - Edge weight = estimated hours to master the target node

Computes ve (earliest finish) and vl (latest finish) to identify the
critical path — the sequence of knowledge nodes that determines the
minimum total study time.  Nodes with ve == vl have zero slack and lie
on the critical path.

All functions are pure: no Flask or database dependencies.
"""


def compute_critical_path(adjacency_map, node_masteries, node_names,
                           mastery_threshold=0.7, hours_per_unit=10.0):
    """
    Compute the critical path through a knowledge DAG.

    Args:
        adjacency_map: dict[prereq_id -> set of dependent_node_ids]
            Edge direction: prereq → dependent (learning order).
        node_masteries: dict[node_id -> float mastery] in [0, 1]
        node_names: dict[node_id -> str name]
        mastery_threshold: nodes above this mastery are considered "done"
        hours_per_unit: base hours per mastery gap unit (default 10)

    Returns:
        dict: {
            total_hours: minimum hours to complete all nodes,
            critical_path: [{node_id, name, mastery, ve, vl, slack, is_critical}],
            all_nodes: [{node_id, name, mastery, ve, vl, slack, is_critical}],
        }
    """
    if not adjacency_map:
        return {'total_hours': 0.0, 'critical_path': [], 'all_nodes': []}

    # Collect all nodes
    all_nodes = set()
    for src, targets in adjacency_map.items():
        all_nodes.add(src)
        all_nodes.update(targets)

    n = len(all_nodes)
    node_list = sorted(all_nodes)  # deterministic ordering

    # ---- Build in-degree and adjacency (prereq → dependent) ----
    # The input adjacency_map is already prereq → set(dependents)
    out_edges = {u: set() for u in all_nodes}
    in_degree = {u: 0 for u in all_nodes}
    for src, targets in adjacency_map.items():
        for tgt in targets:
            if tgt not in out_edges:
                out_edges[tgt] = set()
            out_edges[src].add(tgt)
            in_degree[tgt] = in_degree.get(tgt, 0) + 1
            if src not in in_degree:
                in_degree[src] = 0

    # ---- Edge weight: mastery gap × hours_per_unit ----
    # Weight is the effort to learn the TARGET node
    edge_weight = {}
    for src, targets in adjacency_map.items():
        for tgt in targets:
            gap = max(0.05, mastery_threshold - node_masteries.get(tgt, 0.0))
            weight = round(gap * hours_per_unit, 1)
            edge_weight[(src, tgt)] = weight

    # ---- Topological order (Kahn) ----
    import heapq
    heap = [(0, u) for u in all_nodes if in_degree.get(u, 0) == 0]
    heapq.heapify(heap)
    topo = []
    while heap:
        _, u = heapq.heappop(heap)
        topo.append(u)
        for v in out_edges.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                heapq.heappush(heap, (0, v))

    # If cycle or disconnected nodes remain, add them
    remaining = all_nodes - set(topo)
    topo.extend(sorted(remaining))

    # ---- Forward pass: compute ve (earliest finish) ----
    ve = {u: 0.0 for u in all_nodes}
    for u in topo:
        for v in out_edges.get(u, []):
            w = edge_weight.get((u, v), 0.1)
            if ve[u] + w > ve.get(v, 0.0):
                ve[v] = ve[u] + w

    # ---- Backward pass: compute vl (latest finish) ----
    max_ve = max(ve.values()) if ve else 0.0
    vl = {u: max_ve for u in all_nodes}
    for u in reversed(topo):
        for v in out_edges.get(u, []):
            w = edge_weight.get((u, v), 0.1)
            if vl[v] - w < vl.get(u, max_ve):
                vl[u] = vl[v] - w

    # ---- Compute slack and identify critical nodes ----
    epsilon = 0.001
    results = []
    for u in all_nodes:
        slack = round(vl.get(u, 0) - ve.get(u, 0), 4)
        is_critical = abs(slack) < epsilon
        results.append({
            'node_id': u,
            'name': node_names.get(u, f'node#{u}'),
            'mastery': round(node_masteries.get(u, 0.0), 2),
            'gap': round(max(0, mastery_threshold - node_masteries.get(u, 0.0)), 2),
            've': round(ve.get(u, 0.0), 1),
            'vl': round(vl.get(u, 0.0), 1),
            'slack': slack,
            'is_critical': is_critical,
        })

    critical_path = [r for r in results if r['is_critical']]

    return {
        'total_hours': round(max_ve, 1),
        'critical_path': critical_path,
        'all_nodes': results,
    }
