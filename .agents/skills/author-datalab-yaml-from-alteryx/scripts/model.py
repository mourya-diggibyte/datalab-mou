"""Typed, read-only view of an Alteryx-migration ``conversion_context.json``.

The Alteryx migration tool (``migrations/alteryx/``) converts a ``.yxmd`` workflow into
Databricks PySpark notebooks plus a machine-readable ``conversion_context.json``. That JSON
is the authoritative input for generating datalab YAML artifacts: it carries, per output
table, the medallion ``layer``, the ordered Alteryx tool chain, the generated PySpark of each
tool, and status flags that tell us whether a step was converted by rule, written by a model,
or not converted at all.

This module parses that JSON into small frozen dataclasses so the rest of the generator never
touches raw dicts. It does NOT classify, emit, or call an LLM — it only loads and normalises.
A ``.ipynb`` fallback (when no JSON is present) reconstructs a minimal context from the
notebooks' ``# [id] Kind`` comment markers; it is intentionally lossy and flagged as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# A single Alteryx tool whose logic goes nowhere / writes nothing is dropped, but we keep its
# record so the review file can list it. These status PREFIXES mean "not part of any table".
_DROP_STATUS_PREFIXES = (
    "no effect",
    "not needed",
    "its result goes nowhere",
)

# settings_xml can embed a multi-megabyte cloud table tree (e.g. a BigQuery browser dump); we
# keep only a head so the context stays small and a model prompt never drowns in it.
_SETTINGS_XML_HEAD = 2000


@dataclass(frozen=True)
class Edge:
    """A connection to another tool (``upstream``/``downstream`` entry)."""

    id: int
    name: str
    from_anchor: str = ""
    to_anchor: str = ""


@dataclass(frozen=True)
class Tool:
    """One Alteryx tool as recorded in ``tools[]``."""

    id: int
    name: str
    plugin: str
    container: str
    status: str
    missing: bool
    tables: tuple[str, ...]
    upstream: tuple[Edge, ...]
    downstream: tuple[Edge, ...]
    code: str = ""
    code_table: str = ""
    reason: str = ""
    settings_xml_head: str = ""
    recipe: tuple[str, ...] = ()

    @property
    def is_source(self) -> bool:
        return self.status.startswith("source")

    @property
    def is_output_tool(self) -> bool:
        return self.status.startswith("output tool")

    @property
    def is_dropped(self) -> bool:
        """No effect on the data / dead branch: recorded, never emitted."""
        return self.status.startswith(_DROP_STATUS_PREFIXES)

    @property
    def is_gap(self) -> bool:
        """Needs writing or review: AI-written or not-converted (``missing`` is the flag)."""
        return self.missing


@dataclass(frozen=True)
class Source:
    """One input table the workflow reads (``sources[]``)."""

    table_key: str
    unity_catalog_name: str
    alteryx_tools: tuple[int, ...]
    source_tables: tuple[str, ...]
    query: str = ""
    query_translated: bool = False
    columns: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Table:
    """One output table the notebooks build (``tables[]``) — the unit of one YAML artifact."""

    table: str
    unity_catalog_name: str
    layer: str
    container: str
    notebook: str
    status: str
    reads_sources: tuple[str, ...]
    reads_tables: tuple[str, ...]
    chain: str
    tool_ids: tuple[int, ...]
    spark_sql: bool = False
    no_sql_reason: str = ""
    review_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Context:
    """The whole ``conversion_context.json``, normalised."""

    workflow: str
    sha256: str
    tool_count: int
    sources: tuple[Source, ...]
    tables: tuple[Table, ...]
    tools: tuple[Tool, ...]
    missing: tuple[str, ...]
    macros_missing: tuple[str, ...]
    decisions: dict
    decided_for_you: dict
    source_path: Path | None = None
    from_notebooks: bool = False

    def tool(self, tool_id: int) -> Tool | None:
        return self._by_id.get(tool_id)

    def tools_for(self, table: Table) -> list[Tool]:
        """The table's tools in build order (the order recorded in ``tools[]``)."""
        return [t for tid in table.tool_ids if (t := self._by_id.get(tid)) is not None]

    def source(self, key: str) -> Source | None:
        return next((s for s in self.sources if s.table_key == key), None)

    def decision_for(self, tool_id: int) -> dict | None:
        """The user's or converter's recorded decision for a tool (e.g. Unique's row-keep
        mode), from ``decisions``/``decided_for_you`` keyed by the tool id as a string."""
        key = str(tool_id)
        if key in self.decisions:
            return dict(self.decisions[key])
        entry = self.decided_for_you.get(key)
        if entry and "decision" in entry:
            return dict(entry["decision"])
        return None

    @property
    def _by_id(self) -> dict[int, Tool]:
        # Built lazily; frozen dataclass so stash it on the instance via object.__setattr__.
        cache = self.__dict__.get("_tool_index")
        if cache is None:
            cache = {t.id: t for t in self.tools}
            object.__setattr__(self, "_tool_index", cache)
        return cache


def _edges(raw: list | None) -> tuple[Edge, ...]:
    return tuple(
        Edge(
            id=int(e["id"]),
            name=str(e.get("name", "")),
            from_anchor=str(e.get("from_anchor", "")),
            to_anchor=str(e.get("to_anchor", "")),
        )
        for e in (raw or [])
    )


def _tool(raw: dict) -> Tool:
    settings_xml = str(raw.get("settings_xml", "") or "")
    return Tool(
        id=int(raw["id"]),
        name=str(raw.get("name", "")),
        plugin=str(raw.get("plugin", "")),
        container=str(raw.get("container", "")),
        status=str(raw.get("status", "")),
        missing=bool(raw.get("missing", False)),
        tables=tuple(raw.get("tables", []) or ()),
        upstream=_edges(raw.get("upstream")),
        downstream=_edges(raw.get("downstream")),
        code=str(raw.get("code", "") or ""),
        code_table=str(raw.get("code_table", "") or ""),
        reason=str(raw.get("reason", "") or ""),
        settings_xml_head=settings_xml[:_SETTINGS_XML_HEAD],
        recipe=tuple(raw.get("recipe", []) or ()),
    )


def _source(raw: dict) -> Source:
    cols = raw.get("columns")
    return Source(
        table_key=str(raw["table_key"]),
        unity_catalog_name=str(raw.get("unity_catalog_name", raw["table_key"])),
        alteryx_tools=tuple(int(t) for t in (raw.get("alteryx_tools") or [])),
        source_tables=tuple(raw.get("source_tables", []) or ()),
        query=str(raw.get("query", "") or ""),
        query_translated=bool(raw.get("query_translated", False)),
        columns=tuple(cols) if cols else None,
    )


def _table(raw: dict) -> Table:
    return Table(
        table=str(raw["table"]),
        unity_catalog_name=str(raw.get("unity_catalog_name", raw["table"])),
        layer=str(raw.get("layer", "")),
        container=str(raw.get("container", "")),
        notebook=str(raw.get("notebook", "")),
        status=str(raw.get("status", "")),
        reads_sources=tuple(raw.get("reads_sources", []) or ()),
        reads_tables=tuple(raw.get("reads_tables", []) or ()),
        chain=str(raw.get("chain", "") or ""),
        tool_ids=tuple(int(t) for t in (raw.get("tools") or [])),
        spark_sql=bool(raw.get("spark_sql", False)),
        no_sql_reason=str(raw.get("no_sql_reason", "") or ""),
        review_notes=tuple(raw.get("review_notes", []) or ()),
    )


def load_context(path: str | Path) -> Context:
    """Parse a ``conversion_context.json`` file into a :class:`Context`."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Context(
        workflow=str(data.get("workflow", path.stem)),
        sha256=str(data.get("sha256", "")),
        tool_count=int(data.get("tool_count", 0)),
        sources=tuple(_source(s) for s in data.get("sources", [])),
        tables=tuple(_table(t) for t in data.get("tables", [])),
        tools=tuple(_tool(t) for t in data.get("tools", [])),
        missing=tuple(data.get("missing", []) or ()),
        macros_missing=tuple(data.get("macros_missing", []) or ()),
        decisions=dict(data.get("decisions", {}) or {}),
        decided_for_you=dict(data.get("decided_for_you", {}) or {}),
        source_path=path,
    )
