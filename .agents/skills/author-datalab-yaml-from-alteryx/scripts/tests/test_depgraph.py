"""Task 2: the table dependency graph — build order, fan-in/out, cycle/dangling detection."""

from pathlib import Path

import model
from conftest import EXAMPLE_CONTEXT
from depgraph import GraphError, build, render_order

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_example_orders_bronze_source_before_the_gold_table_that_reads_it():
    ctx = model.load_context(EXAMPLE_CONTEXT)
    graph = build(ctx)

    assert graph.order.index("bronze_xlsx_ba_master") < graph.order.index(
        "gold_xlsx_ba_master_selected"
    )
    assert not graph.fan_in
    assert not graph.fan_out

    rendered = render_order(graph, ctx)
    assert "`bronze_xlsx_ba_master` (source)" in rendered
    assert "`gold_xlsx_ba_master_selected` (gold)" in rendered


def test_cycle_is_detected_and_raises_before_writing_anything():
    ctx = model.load_context(FIXTURES / "cycle_context.json")
    try:
        build(ctx)
    except GraphError as exc:
        assert "silver_a" in str(exc) or "silver_b" in str(exc)
        assert "cycle" in str(exc).lower()
    else:
        raise AssertionError("expected a GraphError for the silver_a <-> silver_b cycle")


def test_dangling_reference_is_detected_with_a_clear_message():
    ctx = model.load_context(FIXTURES / "dangling_context.json")
    try:
        build(ctx)
    except GraphError as exc:
        msg = str(exc)
        assert "gold_orphan" in msg
        assert "silver_never_produced" in msg
    else:
        raise AssertionError("expected a GraphError for the dangling silver_never_produced ref")


def test_fan_in_and_fan_out_are_reported():
    ctx = model.Context(
        workflow="fanin.yxmd",
        sha256="",
        tool_count=0,
        sources=(
            model.Source(
                table_key="bronze_a",
                unity_catalog_name="bronze_a",
                alteryx_tools=(),
                source_tables=(),
            ),
        ),
        tables=(
            model.Table(
                table="silver_x",
                unity_catalog_name="silver_x",
                layer="silver",
                container="C",
                notebook="02_silver.ipynb",
                status="ok",
                reads_sources=("bronze_a",),
                reads_tables=(),
                chain="",
                tool_ids=(),
            ),
            model.Table(
                table="silver_y",
                unity_catalog_name="silver_y",
                layer="silver",
                container="C",
                notebook="02_silver.ipynb",
                status="ok",
                reads_sources=("bronze_a",),
                reads_tables=(),
                chain="",
                tool_ids=(),
            ),
            model.Table(
                table="gold_z",
                unity_catalog_name="gold_z",
                layer="gold",
                container="C",
                notebook="03_gold.ipynb",
                status="ok",
                reads_sources=(),
                reads_tables=("silver_x", "silver_y"),
                chain="",
                tool_ids=(),
            ),
        ),
        tools=(),
        missing=(),
        macros_missing=(),
        decisions={},
        decided_for_you={},
    )
    graph = build(ctx)
    assert graph.fan_out["bronze_a"] == ("silver_x", "silver_y")
    assert set(graph.fan_in["gold_z"]) == {"silver_x", "silver_y"}
    assert graph.order.index("bronze_a") < graph.order.index("silver_x")
    assert graph.order.index("silver_y") < graph.order.index("gold_z")
