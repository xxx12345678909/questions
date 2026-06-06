"""
Graph cycle detection and transitive reduction — pure 408 data-structure algorithms.

Provides:
  - Tarjan SCC (Strongly Connected Components) cycle detection for DAG enforcement
  - Transitive closure redundancy pruning to eliminate indirect dependency edges

All functions are pure: no Flask or database dependencies.
"""


# ================================================================
# Tarjan SCC — cycle detection for DAG integrity
# ================================================================

def verify_graph_cycle_tarjan(num_nodes, graph_adjacency_map):
    """
    Run Tarjan's SCC algorithm to detect cycles in a directed graph.

    Used as a hard-fuse gate: any edge insertion that would create a strongly
    connected component of size > 1 (i.e., a directed cycle) is rejected.

    Args:
        num_nodes: total number of nodes (int, informational)
        graph_adjacency_map: dict[node_id -> list of neighbour node_ids]

    Returns:
        bool: True if a cycle (SCC size > 1) is detected, False otherwise
    """
    dfn = {}
    low = {}
    stack = []
    in_stack = set()
    timer = [0]
    cycle_detected = [False]

    def tarjan_dfs(u):
        dfn[u] = low[u] = timer[0]
        timer[0] += 1
        stack.append(u)
        in_stack.add(u)

        for v in graph_adjacency_map.get(u, []):
            if v not in dfn:
                tarjan_dfs(v)
                low[u] = min(low[u], low[v])
            elif v in in_stack:
                low[u] = min(low[u], dfn[v])

        if low[u] == dfn[u]:
            component_size = 0
            while True:
                node = stack.pop()
                in_stack.remove(node)
                component_size += 1
                if node == u:
                    break
            if component_size > 1:
                cycle_detected[0] = True

    for current_node in list(graph_adjacency_map.keys()):
        if current_node not in dfn:
            tarjan_dfs(current_node)

    return cycle_detected[0]


# ================================================================
# Transitive reduction — prune redundant indirect dependency edges
# ================================================================

def execute_transitive_reduction(nodes_list, edges_tuple_list):
    """
    Compute a transitive reduction on a DAG: remove edges for which an
    alternative indirect path already exists.

    For every direct edge (source, target), if there exists a path from
    source to target that does NOT use this direct edge, the direct edge
    is redundant and should be removed.

    Args:
        nodes_list: list of all node IDs in the graph
        edges_tuple_list: list of (source_id, target_id) directed edges

    Returns:
        list of (source_id, target_id) that are NOT redundant (the reduced set)
    """
    # Build adjacency set for fast lookup
    adjacency = {u: set() for u in nodes_list}
    for s, d in edges_tuple_list:
        adjacency[s].add(d)

    def dfs_has_indirect(start, target, current, visited):
        """Check if there is an indirect path from start to target
        without using the direct edge (start, target)."""
        if current == target and start != current:
            return True
        visited.add(current)
        for next_node in adjacency.get(current, []):
            # Skip the direct edge we are testing
            if current == start and next_node == target:
                continue
            if next_node not in visited:
                if dfs_has_indirect(start, target, next_node, visited):
                    return True
        return False

    redundants = set()
    for s, d in edges_tuple_list:
        if dfs_has_indirect(s, d, s, set()):
            redundants.add((s, d))

    return [edge for edge in edges_tuple_list if edge not in redundants]
