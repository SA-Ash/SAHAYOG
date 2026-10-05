from collections import defaultdict

import networkx as nx


def detect_patterns(nodes, edges):
    graph = nx.DiGraph()
    by_id = {str(n.id): n for n in nodes}
    grouped = defaultdict(list)
    for e in edges:
        graph.add_edge(str(e.from_node), str(e.to_node))
        grouped[str(e.from_node)].append(e)
    findings = []
    for origin in graph:
        outs = grouped[origin]
        if len(outs) == 2:
            total = sum(int(e.amount) for e in outs)
            large = max(outs, key=lambda e: int(e.amount))
            small = min(outs, key=lambda e: int(e.amount))
            if total and int(small.amount) * 100 < total * 15:
                path = [origin]
                current = str(large.to_node)
                seen = set(path)
                while current not in seen:
                    seen.add(current)
                    next_edges = grouped[current]
                    if len(next_edges) != 2:
                        break
                    amount = sum(int(e.amount) for e in next_edges)
                    little = min(next_edges, key=lambda e: int(e.amount))
                    if not amount or int(little.amount) * 100 >= amount * 15:
                        break
                    path.append(current)
                    current = str(max(next_edges, key=lambda e: int(e.amount)).to_node)
                if len(path) >= 4 and not any(
                    f["kind"] == "PEEL" and origin in f["nodes"] for f in findings
                ):
                    findings.append(
                        {
                            "kind": "PEEL",
                            "nodes": path,
                            "score": 0.85,
                            "evidence": [
                                {
                                    "signal": "peel_chain",
                                    "consecutive_hops": len(path),
                                    "small_output_ratio": "<.15",
                                    "probabilistic": True,
                                }
                            ],
                        }
                    )
        targets = list(graph.successors(origin))
        for i, left in enumerate(targets):
            for right in targets[i + 1 :]:
                common = set(graph.successors(left)) & set(graph.successors(right))
                for merge in common:
                    involved = [
                        e
                        for e in edges
                        if (str(e.from_node), str(e.to_node))
                        in {(origin, left), (origin, right), (left, merge), (right, merge)}
                    ]
                    if (
                        involved
                        and (
                            max(e.block_time for e in involved)
                            - min(e.block_time for e in involved)
                        ).total_seconds()
                        <= 3600
                    ):
                        findings.append(
                            {
                                "kind": "LAYERING",
                                "nodes": [origin, left, right, merge],
                                "score": 0.8,
                                "evidence": [
                                    {"signal": "split_then_merge", "window_seconds": 3600}
                                ],
                            }
                        )
    roots = [n for n in graph if graph.in_degree(n) == 0]
    stack = [(root, [root], 0) for root in roots]
    count = 0
    while stack and count < 5000:
        current, path, changes = stack.pop()
        count += 1
        for target in graph.successors(current):
            if target in path:
                continue
            next_changes = changes + (by_id[current].chain != by_id[target].chain)
            if next_changes >= 2:
                findings.append(
                    {
                        "kind": "CHAIN_HOP",
                        "nodes": path + [target],
                        "score": 0.9,
                        "evidence": [{"signal": "multiple_chain_changes", "count": next_changes}],
                    }
                )
            else:
                stack.append((target, path + [target], next_changes))
    for e in edges:
        if e.inferred:
            findings.append(
                {
                    "kind": "SWAP_INFERRED",
                    "nodes": [str(e.from_node), str(e.to_node)],
                    "score": 0.7,
                    "evidence": e.evidence_json,
                }
            )
    unique = {f["kind"] + ":" + ",".join(f["nodes"]): f for f in findings}
    return list(unique.values())
