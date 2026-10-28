"""Build the output-table dependency graph before any YAML is emitted.

Each :class:`~model.Table` records what it reads (``reads_sources``, ``reads_tables``). This
module turns those edges into a DAG over table KEYS (sources are leaves, output tables are
nodes), so the rest of the generator can:

- emit files in a safe build order (bronze before the silver/gold that reads it),
- tell a single-upstream table apart from a fan-in (2+ upstreams: ``sources:`` array or a
  transform with ``tables:`` kwargs) and a fan-out (one table feeding many consumers),
- fail fast — before writing anything — on a cycle or a reference to a table that is neither
  a declared source nor produced by any ``tables[]`` entry.

This module does NOT classify tool logic or decide bronze/silver/gold constructs; it only
orders and validates the table-level graph. See ``classify.py`` for the per-tool decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from model import Context


class GraphError(Exception):
    """A cycle or a dangling reference — always raised before any file is written."""


@dataclass(frozen=True)
class Edge:
    """One ``upstream -> downstream`` table dependency."""

    upstream: str
    downstream: str


@dataclass(frozen=True)
class DependencyGraph:
    """The resolved table DAG: topological build order + the raw edges."""

    order: tuple[str, ...]  # table/source keys, upstream-first
    edges: tuple[Edge, ...]
    fan_in: dict[str, tuple[str, ...]]  # table -> upstreams, where len(upstreams) >= 2
    fan_out: dict[str, tuple[str, ...]]  # table/source -> downstream tables, where >= 2


def build(ctx: Context) -> DependencyGraph:
    """Topologically order ``ctx.tables`` and detect fan-in/out, cycles and dangling refs.

    Raises :class:`GraphError` (never a partial result) so the caller can abort cleanly before
    writing any file. Call this once, before any emission task runs.
    """
    table_keys = {t.table for t in ctx.tables}
    source_keys = {s.table_key for s in ctx.sources}
    known = table_keys | source_keys

    upstreams: dict[str, tuple[str, ...]] = {}
    downstreams: dict[str, list[str]] = {k: [] for k in known}
    edges: list[Edge] = []

    for tbl in ctx.tables:
        ups = tuple(dict.fromkeys([*tbl.reads_sources, *tbl.reads_tables]))  # de-dup, keep order
        dangling = [u for u in ups if u not in known]
        if dangling:
            raise GraphError(
                f"table '{tbl.table}' reads {dangling}, which is neither a declared source "
                "nor an entry in tables[] — fix conversion_context.json or the workflow before "
                "generating YAML."
            )
        upstreams[tbl.table] = ups
        for u in ups:
            downstreams.setdefault(u, []).append(tbl.table)

    order = _topological_sort(known, upstreams)

    fan_in = {t: ups for t, ups in upstreams.items() if len(ups) >= 2}
    fan_out = {k: tuple(ds) for k, ds in downstreams.items() if len(ds) >= 2}
    for downstream, ups in upstreams.items():
        for up in ups:
            edges.append(Edge(upstream=up, downstream=downstream))

    return DependencyGraph(order=order, edges=tuple(edges), fan_in=fan_in, fan_out=fan_out)


def _topological_sort(nodes: set[str], upstreams: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Kahn's algorithm. Sources (no recorded upstreams) sort first, deterministically."""
    deps = {n: list(upstreams.get(n, ())) for n in nodes}
    remaining = {n: set(deps[n]) for n in nodes}
    ready = sorted(n for n, ups in remaining.items() if not ups)  # sorted = deterministic order
    result: list[str] = []
    consumers: dict[str, list[str]] = {n: [] for n in nodes}
    for n, ups in deps.items():
        for u in ups:
            consumers[u].append(n)

    while ready:
        node = ready.pop(0)
        result.append(node)
        for consumer in sorted(consumers.get(node, [])):
            remaining[consumer].discard(node)
            if not remaining[consumer] and consumer not in result and consumer not in ready:
                ready.append(consumer)
        ready.sort()

    if len(result) != len(nodes):
        stuck = sorted(nodes - set(result))
        raise GraphError(
            "a cycle was detected among these tables (each still waits on another): "
            f"{stuck} — datalab pipelines are DAGs; a cyclic workflow cannot be converted as is."
        )
    return tuple(result)


def render_order(graph: DependencyGraph, ctx: Context) -> str:
    """A short ``## Dependency order`` block for migration_review.md."""
    layer_of = {t.table: t.layer for t in ctx.tables}
    lines = ["## Dependency order", ""]
    for i, key in enumerate(graph.order, start=1):
        layer = layer_of.get(key, "source")
        lines.append(f"{i}. `{key}` ({layer})")
    if graph.fan_in:
        lines += ["", "### Fan-in (2+ upstreams)"]
        lines += [f"- `{t}` <- {', '.join(ups)}" for t, ups in graph.fan_in.items()]
    if graph.fan_out:
        lines += ["", "### Fan-out (feeds 2+ tables)"]
        lines += [f"- `{k}` -> {', '.join(ds)}" for k, ds in graph.fan_out.items()]
    return "\n".join(lines)
