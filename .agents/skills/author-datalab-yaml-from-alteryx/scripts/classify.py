"""Classify each Alteryx tool feeding a table into a datalab construct, with a confidence.

Three buckets per tool (see the plan / ``references/tool-mapping.md`` for the full table):

- ``yaml``   — pure declarative: a ``columns:`` entry or a ``quality:`` rule. No Python.
- ``transform`` — needs a ``@transform`` function: a grain change, a join/union, a window,
  an orderless dedupe, or a formula that cannot be written as one declarative column.
- ``drop``   — no effect on the data, a dead branch, or an output tool the datalab target
  table makes redundant (the table IS the sink). Recorded, never emitted.
- ``gap``    — a step that needs writing or review before it can run at all (``missing``,
  a missing macro, an unrecognised tool). Routed to the LLM / a human, never silently
  finalised.

Confidence (``high``/``medium``/``low``, plus a 0-1 score) is set per tool and rolled up to
the table: the table's confidence is its weakest tool's confidence, because one shaky step
makes the whole artifact worth a second look. ``classify.py`` makes decisions; it does not
emit YAML (``emit.py``) or call a model (``llm_gaps.py``).

The core rule enforced here: **a ``@transform`` is emitted only when YAML genuinely cannot
express the logic.** Every ``transform`` decision carries a ``reason`` so a reviewer can see
one was not created gratuitously, and a table with no ``transform``-bucket tool gets no stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from model import Context, Table, Tool

# Tool NAMES whose logic is always expressible as pure YAML (rename/cast/filter-shaped).
_YAML_TOOL_NAMES = {"Select", "Filter"}

# Tool NAMES that always force a transform: they change grain, combine multiple inputs, or
# depend on row order/window state that a single declarative column cannot express.
_TRANSFORM_TOOL_NAMES = {
    "Summarize",
    "Cross Tab",
    "Join",
    "Union",
    "Append Fields",
    "Multi-Row Formula",
    "Running Total",
    "Record ID",
    "Generate Rows",
    "Sort",  # order only matters if something downstream depends on it; treated conservatively
}

# Statuses that mean "no effect / not part of any output" — dropped, never emitted.
_DROP_STATUS_PREFIXES = (
    "no effect",
    "not needed",
    "its result goes nowhere",
)


class Bucket(StrEnum):
    YAML = "yaml"
    TRANSFORM = "transform"
    DROP = "drop"
    GAP = "gap"
    WIRING = "wiring"  # a source tool: becomes the table's source:, not a per-tool construct


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_CONFIDENCE_SCORE = {Confidence.HIGH: 0.9, Confidence.MEDIUM: 0.6, Confidence.LOW: 0.25}
_CONFIDENCE_ORDER = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


@dataclass(frozen=True)
class ToolDecision:
    """Where one tool's logic lands, and how sure we are."""

    tool_id: int
    name: str
    bucket: Bucket
    confidence: Confidence
    score: float
    reason: str
    construct: str  # short label, e.g. "columns: rename/cast", "@transform: dedupe (no order)"


@dataclass(frozen=True)
class TableDecision:
    """The rolled-up decision for one output table."""

    table: str
    layer: str
    tool_decisions: tuple[ToolDecision, ...]
    confidence: Confidence
    needs_transform: bool
    gaps: tuple[ToolDecision, ...] = field(default_factory=tuple)
    dropped: tuple[ToolDecision, ...] = field(default_factory=tuple)
    review_notes: tuple[str, ...] = field(default_factory=tuple)


def classify_tool(tool: Tool, ctx: Context | None = None) -> ToolDecision:
    """Classify ONE tool. ``ctx`` is needed only for tools whose decision lives in the
    context's top-level ``decisions``/``decided_for_you`` (currently: ``Unique``)."""
    if tool.is_gap:
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.GAP,
            confidence=Confidence.LOW,
            score=_CONFIDENCE_SCORE[Confidence.LOW],
            reason=f"status '{tool.status}' — not safely convertible by rule",
            construct="ALTERYX-GAP: route to LLM / review",
        )

    if tool.is_source:
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.WIRING,
            confidence=Confidence.HIGH,
            score=_CONFIDENCE_SCORE[Confidence.HIGH],
            reason="source tool: becomes the table's source:",
            construct="source wiring",
        )

    if tool.is_output_tool:
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.DROP,
            confidence=Confidence.MEDIUM,
            score=_CONFIDENCE_SCORE[Confidence.MEDIUM],
            reason="output tool: the datalab table IS the sink — export recorded, not emitted",
            construct=f"# EXPORT (not wired): {tool.reason or 'see settings_xml'}",
        )

    if tool.is_dropped or tool.status.startswith(_DROP_STATUS_PREFIXES):
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.DROP,
            confidence=Confidence.HIGH,
            score=_CONFIDENCE_SCORE[Confidence.HIGH],
            reason=f"status '{tool.status}'",
            construct="dropped on purpose",
        )

    if tool.name == "Unique":
        return _classify_unique(tool, ctx)

    if tool.name == "Formula":
        return _classify_formula(tool)

    if tool.name in _TRANSFORM_TOOL_NAMES:
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.TRANSFORM,
            confidence=Confidence.HIGH,
            score=_CONFIDENCE_SCORE[Confidence.HIGH],
            reason=f"{tool.name} changes grain, order, or combines inputs — not a single column",
            construct=f"@transform: {tool.name.lower().replace(' ', '_')}",
        )

    if tool.name in _YAML_TOOL_NAMES:
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.YAML,
            confidence=Confidence.HIGH,
            score=_CONFIDENCE_SCORE[Confidence.HIGH],
            reason=f"{tool.name} is a rename/cast/drop or row filter — a declarative column/rule",
            construct="columns:" if tool.name == "Select" else "quality: filter rule",
        )

    # An unrecognised tool that the migration tool nonetheless marked "converted by rule":
    # trust it, but at medium confidence — it is outside our known-tool vocabulary.
    if tool.status == "converted by rule":
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.TRANSFORM,
            confidence=Confidence.MEDIUM,
            score=_CONFIDENCE_SCORE[Confidence.MEDIUM],
            reason=f"'{tool.name}' is not in the known tool vocabulary; treated as a transform",
            construct=f"@transform: {tool.name.lower().replace(' ', '_')} (review)",
        )

    # Anything else (unknown status, unknown tool) is a gap by default — never guess silently.
    return ToolDecision(
        tool_id=tool.id,
        name=tool.name,
        bucket=Bucket.GAP,
        confidence=Confidence.LOW,
        score=_CONFIDENCE_SCORE[Confidence.LOW],
        reason=f"unrecognised tool '{tool.name}' with status '{tool.status}'",
        construct="ALTERYX-GAP: route to LLM / review",
    )


def _classify_unique(tool: Tool, ctx: Context | None) -> ToolDecision:
    """Unique with an order key -> CDC; an orderless 'any row' keep -> a dropDuplicates
    transform. The row-keep mode lives in the context's top-level ``decisions`` /
    ``decided_for_you`` (keyed by tool id), NOT on the tool record itself — the tool JSON
    carries no ``reason`` for Unique. Mapping an orderless dedupe to CDC would silently pick
    different survivors than Alteryx did, so confidence is MEDIUM even when the mapping
    itself (dropDuplicates) is correct; an unknown mode is a GAP, never a silent guess.
    """
    decision = ctx.decision_for(tool.id) if ctx is not None else None
    mode = (decision or {}).get("mode")

    if mode == "any":
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.TRANSFORM,
            confidence=Confidence.MEDIUM,
            score=_CONFIDENCE_SCORE[Confidence.MEDIUM],
            reason="Unique keeps 'any' matching row (no order key, decisions[].mode=any) — "
            "dropDuplicates, NOT cdc (cdc would pick a different, order-dependent survivor)",
            construct="@transform: dropDuplicates (any-row semantics)",
        )
    if mode in ("first", "last"):
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.TRANSFORM,
            confidence=Confidence.MEDIUM,
            score=_CONFIDENCE_SCORE[Confidence.MEDIUM],
            reason=f"Unique keeps the '{mode}' matching row — an explicit order key exists; "
            "candidate for target.cdc, review the key/sequence before switching",
            construct="target.cdc (review) or @transform: dedupe (ordered)",
        )

    # No recorded decision at all: we cannot tell whether order matters without guessing —
    # that is exactly the silent-divergence risk this rule exists to avoid.
    return ToolDecision(
        tool_id=tool.id,
        name=tool.name,
        bucket=Bucket.GAP,
        confidence=Confidence.LOW,
        score=_CONFIDENCE_SCORE[Confidence.LOW],
        reason="Unique has no recorded row-keep decision (decisions/decided_for_you) — "
        "cannot tell cdc vs dropDuplicates without guessing",
        construct="ALTERYX-GAP: confirm Unique row-keep mode",
    )


def _classify_formula(tool: Tool) -> ToolDecision:
    """A Formula that only assigns a literal / current_timestamp / a plain cast is a single
    declarative column; anything referencing multiple columns, a conditional, or a regex is a
    transform. We inspect the generated ``code`` (not the Alteryx expression) since that is
    what the migration tool already decided to emit.
    """
    code = tool.code.lower()
    simple_markers = ("current_timestamp", "date_format", ".cast(", "lit(")
    multi_column_markers = ("f.when", "f.col(", "f.regexp_replace", "f.concat", "f.udf")
    looks_simple = any(m in code for m in simple_markers)
    looks_complex = any(m in code for m in multi_column_markers) and "withcolumn" in code

    if looks_simple and not looks_complex:
        return ToolDecision(
            tool_id=tool.id,
            name=tool.name,
            bucket=Bucket.YAML,
            confidence=Confidence.MEDIUM,
            score=_CONFIDENCE_SCORE[Confidence.MEDIUM],
            reason="Formula assigns a literal/current_timestamp/cast — a derived column",
            construct="columns: derived (review expression support at the pinned framework)",
        )
    return ToolDecision(
        tool_id=tool.id,
        name=tool.name,
        bucket=Bucket.TRANSFORM,
        confidence=Confidence.MEDIUM,
        score=_CONFIDENCE_SCORE[Confidence.MEDIUM],
        reason="Formula expression is not a single declarative column (conditional/multi-column)",
        construct="@transform: formula",
    )


def classify_table(ctx: Context, table: Table) -> TableDecision:
    """Classify every tool feeding ``table`` and roll the result up to a table decision."""
    decisions = tuple(classify_tool(t, ctx) for t in ctx.tools_for(table))
    gaps = tuple(d for d in decisions if d.bucket is Bucket.GAP)
    dropped = tuple(d for d in decisions if d.bucket is Bucket.DROP)
    needs_transform = any(d.bucket is Bucket.TRANSFORM for d in decisions)

    confidences = [d.confidence for d in decisions] or [Confidence.HIGH]
    table_confidence = min(confidences, key=lambda c: _CONFIDENCE_ORDER[c])
    if gaps:
        table_confidence = Confidence.LOW

    return TableDecision(
        table=table.table,
        layer=table.layer,
        tool_decisions=decisions,
        confidence=table_confidence,
        needs_transform=needs_transform,
        gaps=gaps,
        dropped=dropped,
        review_notes=table.review_notes,
    )


def classify_all(ctx: Context) -> dict[str, TableDecision]:
    """Classify every table in the context. Order follows ``ctx.tables``."""
    return {t.table: classify_table(ctx, t) for t in ctx.tables}
