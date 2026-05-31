"""
Pure stateless DAG topological path planning.
No Flask or database dependencies — operates on pre-fetched data structures.
"""


def build_topo_learning_path(target_node_id, target_name, adjacency_tree,
                              node_masteries, node_names, mastery_threshold=0.7):
    """
    Pure function: given pre-fetched dependency tree and node metadata,
    compute the topological learning path.

    Algorithm:
    1. Build in-degree map from adjacency tree
    2. Topological sort prioritized by lowest mastery first
    3. Annotate each node with status (BLOCKING/WARNING/OK/TARGET)

    Args:
        target_node_id: int, the target knowledge node ID
        target_name: str, display name of target node
        adjacency_tree: dict[node_id -> set of prereq_ids]
        node_masteries: dict[node_id -> float mastery]
        node_names: dict[node_id -> str name]
        mastery_threshold: float, threshold for needing review (default 0.7)

    Returns:
        dict: {target_node, estimated_hours, path: [{step, node_id, name, mastery, status}]}
    """
    # Collect all nodes involved
    all_nodes = {target_node_id}
    for node_id, prereqs in adjacency_tree.items():
        all_nodes.add(node_id)
        all_nodes.update(prereqs)

    # Compute in-degree: how many nodes depend on each node
    in_degree = {nid: 0 for nid in all_nodes}
    for node_id, prereqs in adjacency_tree.items():
        for prereq_id in prereqs:
            in_degree[prereq_id] = in_degree.get(prereq_id, 0) + 1

    # Topological sort: prioritize lowest-mastery nodes first
    sorted_path = []
    remaining = set(all_nodes)
    zero_in = [nid for nid in remaining if in_degree.get(nid, 0) == 0]

    while zero_in:
        zero_in.sort(key=lambda nid: (node_masteries.get(nid, 1.0), nid))
        current = zero_in.pop(0)

        if current != target_node_id:
            sorted_path.append(current)
        remaining.discard(current)

        if current in adjacency_tree:
            for prereq_id in adjacency_tree[current]:
                in_degree[prereq_id] -= 1
                if in_degree[prereq_id] == 0 and prereq_id in remaining:
                    zero_in.append(prereq_id)

    # Add any remaining nodes (sorted by mastery), target node last
    remaining_list = sorted(remaining, key=lambda nid: node_masteries.get(nid, 1.0))
    sorted_path.extend(remaining_list)

    # Build output
    path = []
    step = 1
    for nid in sorted_path:
        m = node_masteries.get(nid, 0.0)
        if nid == target_node_id:
            status = 'TARGET'
        elif m < 0.4:
            status = 'BLOCKING'
        elif m < mastery_threshold:
            status = 'WARNING'
        else:
            status = 'OK'
        path.append({
            'step': step,
            'node_id': nid,
            'name': node_names.get(nid, f'node#{nid}'),
            'mastery': round(m, 4),
            'status': status,
        })
        step += 1

    # Estimate effort: mastery gap / 0.15 hours per node
    total_gap = sum(1.0 - node_masteries.get(nid, 0) for nid in sorted_path if nid != target_node_id)
    estimated_hours = round(total_gap / 0.15, 1)

    return {
        'target_node': target_name,
        'estimated_hours': max(0.1, estimated_hours),
        'path': path,
    }
