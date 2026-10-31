"""Service dependency graph built from parent/child spans and explicit dependency events.

Edges point from caller to callee. Failures usually propagate *against* edge direction:
when a callee breaks, its callers (transitively) see errors and latency. Every edge keeps
the IDs of the events it was derived from so the UI can show why the edge exists.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.analysis.types import Event, GraphEdge

MAX_EDGE_EVIDENCE = 10


@dataclass(slots=True)
class ServiceGraph:
    nodes: set[str] = field(default_factory=set)
    edges: dict[tuple[str, str], GraphEdge] = field(default_factory=dict)

    def callees(self, service: str) -> set[str]:
        return {t for (s, t) in self.edges if s == service}

    def callers(self, service: str) -> set[str]:
        return {s for (s, t) in self.edges if t == service}

    def _walk(self, start: str, step) -> set[str]:
        seen: set[str] = set()
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for nxt in step(current):
                if nxt not in seen and nxt != start:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def transitive_callers(self, service: str) -> set[str]:
        """Services that (directly or indirectly) depend on ``service`` — its blast radius."""
        return self._walk(service, self.callers)

    def transitive_callees(self, service: str) -> set[str]:
        return self._walk(service, self.callees)

    def undirected_distance(self, a: str, b: str, max_hops: int) -> int | None:
        if a == b:
            return 0
        adjacency: dict[str, set[str]] = defaultdict(set)
        for s, t in self.edges:
            adjacency[s].add(t)
            adjacency[t].add(s)
        frontier, seen = {a}, {a}
        for hops in range(1, max_hops + 1):
            frontier = {n for f in frontier for n in adjacency[f]} - seen
            if b in frontier:
                return hops
            if not frontier:
                return None
            seen |= frontier
        return None

    def depth_order(self) -> dict[str, int]:
        """Layer index for layout: entry points (no callers) are 0, callees are deeper."""
        depth = {n: 0 for n in self.nodes}
        for _ in range(len(self.nodes)):
            changed = False
            for s, t in self.edges:
                if s != t and depth[t] < depth[s] + 1 and depth[s] + 1 < len(self.nodes):
                    depth[t] = depth[s] + 1
                    changed = True
            if not changed:
                break
        return depth


def build_graph(events: Iterable[Event]) -> ServiceGraph:
    graph = ServiceGraph()
    spans: dict[tuple[str, str], Event] = {}
    children: list[Event] = []
    for event in events:
        graph.nodes.add(event.service)
        if event.event_type == "span" and event.trace_id and event.span_id:
            spans[(event.trace_id, event.span_id)] = event
            if event.parent_span_id:
                children.append(event)
        elif event.event_type == "dependency":
            target = event.attributes.get("target") or event.attributes.get("depends_on")
            if isinstance(target, str) and target and target != event.service:
                graph.nodes.add(target)
                edge = graph.edges.setdefault((event.service, target), GraphEdge(event.service, target))
                edge.origins.add("explicit")
                if len(edge.evidence_event_ids) < MAX_EDGE_EVIDENCE:
                    edge.evidence_event_ids.append(event.id)

    for child in children:
        parent = spans.get((child.trace_id, child.parent_span_id))  # type: ignore[arg-type]
        if parent is None or parent.service == child.service:
            continue
        edge = graph.edges.setdefault((parent.service, child.service), GraphEdge(parent.service, child.service))
        edge.origins.add("span")
        edge.call_count += 1
        if child.is_error:
            edge.error_count += 1
        if child.duration_ms is not None:
            edge.durations.append(child.duration_ms)
        if len(edge.evidence_event_ids) < MAX_EDGE_EVIDENCE:
            edge.evidence_event_ids.append(child.id)
    return graph


def error_trace_origins(events: Iterable[Event]) -> dict[str, tuple[str, str]]:
    """For each trace containing failing spans, find the deepest failing span — the failing
    span none of whose children failed. Returns {trace_id: (origin_service, span_event_id)}.
    """
    by_trace: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.event_type == "span" and event.trace_id and event.span_id:
            by_trace[event.trace_id].append(event)
    origins: dict[str, tuple[str, str]] = {}
    for trace_id, spans in by_trace.items():
        failing = [s for s in spans if s.is_error]
        if not failing:
            continue
        failing_parents = {s.parent_span_id for s in failing if s.parent_span_id}
        leaves = [s for s in failing if s.span_id not in failing_parents]
        # Several independent failing leaves: pick the earliest one.
        origin = min(leaves or failing, key=lambda s: (s.occurred_at, s.span_id))
        origins[trace_id] = (origin.service, origin.id)
    return origins
