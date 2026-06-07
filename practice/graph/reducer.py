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

    [Complexity] Time: O(V + E)  Space: O(V) — standard Tarjan DFS
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

    For every direct edge (s, d), if there exists a neighbour w of s (w != d)
    such that d is reachable from w, then the edge (s,d) is redundant because
    s → w → ... → d is an indirect path.

    Algorithm (optimised):
      Phase 1 — Precompute reachability for every node via iterative DFS.
                O(V * (V+E)) total.
      Phase 2 — For each edge (s,d), scan adjacency[s] checking whether d is
                in reachable[w] for some w ≠ d.  O(E * avg_out_degree).

    Since knowledge graphs are DAGs and typically sparse (avg_out_degree ≪ V),
    Phase 2 is effectively O(E) and the total is dominated by Phase 1.

    Args:
        nodes_list: list of all node IDs in the graph
        edges_tuple_list: list of (source_id, target_id) directed edges

    Returns:
        list of (source_id, target_id) that are NOT redundant (the reduced set)

    [Complexity] Time: O(V * (V+E)) — reachability precomputation dominates
                Space: O(V²) — |V| reachability sets of size up to |V|
    """
    if not edges_tuple_list:
        return []

    # Build adjacency
    adjacency = {u: set() for u in nodes_list}
    for s, d in edges_tuple_list:
        adjacency[s].add(d)

    # ---- Phase 1: precompute reachability for every node ----
    # reachable[u] = all nodes v != u such that u can reach v
    reachable = {}
    for u in nodes_list:
        visited = set()
        stack = [u]
        while stack:
            v = stack.pop()
            if v in visited:
                continue
            visited.add(v)
            for w in adjacency.get(v, []):
                if w not in visited:
                    stack.append(w)
        visited.discard(u)          # exclude self
        reachable[u] = visited

    # ---- Phase 2: check each edge for an indirect alternative ----
    redundants = set()
    for s, d in edges_tuple_list:
        for w in adjacency[s]:
            if w != d and d in reachable.get(w, set()):
                redundants.add((s, d))
                break

    return [edge for edge in edges_tuple_list if edge not in redundants]
