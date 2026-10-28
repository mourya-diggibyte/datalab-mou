"""Task 3: the classification engine (YAML vs @transform vs drop vs gap) + confidence."""

from pathlib import Path

import model
from classify import Bucket, Confidence, classify_all, classify_table
from conftest import EXAMPLE_CONTEXT

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_example_select_and_formula_are_yaml_or_review_but_unique_forces_a_transform():
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)

    by_name = {d.name: d for d in decision.tool_decisions}

    # Select is a pure rename/cast/drop -> columns:, high confidence, no transform needed.
    assert by_name["Select"].bucket is Bucket.YAML
    assert by_name["Select"].confidence is Confidence.HIGH

    # Formula only assigns current_timestamp()-derived SYNCDATE -> stays declarative-ish,
    # but is scored medium since an expression column is a judgment call.
    assert by_name["Formula"].bucket in (Bucket.YAML, Bucket.TRANSFORM)
    assert by_name["Formula"].confidence is Confidence.MEDIUM

    # Unique has decisions["9"]["mode"] == "any" (no order key) -> dropDuplicates
    # transform, explicitly NOT cdc, because cdc would pick a different survivor.
    unique = by_name["Unique"]
    assert unique.bucket is Bucket.TRANSFORM
    assert "dropduplicates" in unique.construct.lower()
    assert "cdc" not in unique.construct.lower()

    # The table therefore DOES need a transform (because of Unique), at medium confidence.
    assert decision.needs_transform is True
    assert decision.confidence is Confidence.MEDIUM
    assert not decision.gaps  # nothing in this table is a real gap


def test_unique_without_a_recorded_decision_is_a_gap_not_a_guess():
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    # Simulate a workflow where the Unique row-keep mode was never answered.
    ctx_no_decision = model.Context(
        workflow=ctx.workflow,
        sha256=ctx.sha256,
        tool_count=ctx.tool_count,
        sources=ctx.sources,
        tables=ctx.tables,
        tools=ctx.tools,
        missing=ctx.missing,
        macros_missing=ctx.macros_missing,
        decisions={},
        decided_for_you={},
    )
    decision = classify_table(ctx_no_decision, table)
    by_name = {d.name: d for d in decision.tool_decisions}
    assert by_name["Unique"].bucket is Bucket.GAP
    assert by_name["Unique"].confidence is Confidence.LOW
    assert decision.confidence is Confidence.LOW
    assert len(decision.gaps) == 1


def test_pure_rename_cast_filter_table_needs_no_transform():
    """The '@transform only when truly needed' rule: a table whose only tools are Select
    and Filter must come back with needs_transform=False and zero TRANSFORM decisions."""
    ctx = model.Context(
        workflow="pure_yaml.yxmd",
        sha256="",
        tool_count=2,
        sources=(
            model.Source(
                table_key="bronze_a", unity_catalog_name="bronze_a",
                alteryx_tools=(), source_tables=(),
            ),
        ),
        tables=(
            model.Table(
                table="silver_pure", unity_catalog_name="silver_pure", layer="silver",
                container="C", notebook="02_silver.ipynb", status="ok",
                reads_sources=("bronze_a",), reads_tables=(), chain="Select [1] -> Filter [2]",
                tool_ids=(1, 2),
            ),
        ),
        tools=(
            model.Tool(
                id=1, name="Select", plugin="AlteryxSelect", container="C",
                status="converted by rule", missing=False, tables=("silver_pure",),
                upstream=(), downstream=(), code="df = df.select('a', 'b')",
            ),
            model.Tool(
                id=2, name="Filter", plugin="AlteryxFilter", container="C",
                status="converted by rule", missing=False, tables=("silver_pure",),
                upstream=(), downstream=(), code="df = df.filter(F.col('a').isNotNull())",
            ),
        ),
        missing=(), macros_missing=(), decisions={}, decided_for_you={},
    )
    decision = classify_table(ctx, ctx.tables[0])
    assert decision.needs_transform is False
    assert all(d.bucket is Bucket.YAML for d in decision.tool_decisions)
    assert decision.confidence is Confidence.HIGH


def test_unrecognised_tool_is_a_gap_by_default_never_a_silent_guess():
    ctx = model.Context(
        workflow="weird.yxmd", sha256="", tool_count=1, sources=(),
        tables=(
            model.Table(
                table="gold_weird", unity_catalog_name="gold_weird", layer="gold",
                container="C", notebook="03_gold.ipynb", status="ok",
                reads_sources=(), reads_tables=(), chain="SpatialMatch [1]", tool_ids=(1,),
            ),
        ),
        tools=(
            model.Tool(
                id=1, name="SpatialMatch", plugin="AlteryxSpatialMatch", container="C",
                status="some unknown status", missing=False, tables=("gold_weird",),
                upstream=(), downstream=(),
            ),
        ),
        missing=(), macros_missing=(), decisions={}, decided_for_you={},
    )
    decision = classify_table(ctx, ctx.tables[0])
    assert decision.gaps and decision.gaps[0].name == "SpatialMatch"
    assert decision.confidence is Confidence.LOW


def test_classify_all_covers_every_table():
    ctx = model.load_context(EXAMPLE_CONTEXT)
    decisions = classify_all(ctx)
    assert set(decisions) == {t.table for t in ctx.tables}
