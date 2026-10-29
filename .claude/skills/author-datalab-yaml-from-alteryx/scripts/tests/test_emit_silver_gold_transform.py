"""Tasks 5–7: silver emission, gold emission, @transform stubs + domain.yml imports."""

from pathlib import Path

import emit
import model
from classify import classify_all, classify_table
from conftest import EXAMPLE_CONTEXT

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Task 5: Silver emission
# ---------------------------------------------------------------------------


def _load_silver_ctx():
    return model.load_context(FIXTURES / "silver_context.json")


def test_silver_yml_schema_header_depth(tmp_path):
    ctx = _load_silver_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    p = emit.emit_silver_yml(ctx, table, decision, "silver_test", tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "# yaml-language-server: $schema=../../../../../schemas/table_config.schema.json" in text


def test_silver_yml_columns_extracted_from_select(tmp_path):
    ctx = _load_silver_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_silver_yml(ctx, table, decision, "silver_test", tmp_path).read_text(encoding="utf-8")

    assert "columns:" in text
    # order_id: long cast
    assert "name: order_id" in text
    assert "type: long" in text
    # total: double cast with rename
    assert "name: total_amount" in text
    assert "source: total" in text
    assert "type: double" in text


def test_silver_yml_quality_rule_from_filter(tmp_path):
    ctx = _load_silver_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_silver_yml(ctx, table, decision, "silver_test", tmp_path).read_text(encoding="utf-8")

    # Filter: col('order_id').isNotNull() -> quality: is_not_null
    assert "quality:" in text
    assert "is_not_null" in text
    assert "criticality: error" in text


def test_silver_pure_rename_filter_has_no_transform(tmp_path):
    """A table with only Select + Filter must emit needs_transform=False → no transform: block."""
    ctx = _load_silver_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    assert decision.needs_transform is False
    text = emit.emit_silver_yml(ctx, table, decision, "silver_test", tmp_path).read_text(encoding="utf-8")
    assert "transform:" not in text


def test_silver_yml_source_wiring(tmp_path):
    ctx = _load_silver_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_silver_yml(ctx, table, decision, "silver_test", tmp_path).read_text(encoding="utf-8")
    assert "kind: table" in text
    assert "name: bronze_orders" in text
    assert "layer: bronze" in text
    assert "as_stream: true" in text


def test_silver_yml_streaming_table_target(tmp_path):
    ctx = _load_silver_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_silver_yml(ctx, table, decision, "silver_test", tmp_path).read_text(encoding="utf-8")
    assert "kind: streaming_table" in text


# ---------------------------------------------------------------------------
# Task 6: Gold emission
# ---------------------------------------------------------------------------


def test_gold_yml_schema_header_depth(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    assert table.layer == "gold"
    decision = classify_table(ctx, table)
    p = emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "# yaml-language-server: $schema=../../../../../schemas/table_config.schema.json" in text


def test_gold_yml_uses_materialized_view(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path).read_text(encoding="utf-8")
    assert "kind: materialized_view" in text


def test_gold_yml_has_transform_block_for_unique_any(tmp_path):
    """Unique(any) forces a @transform — the gold YAML must reference the function."""
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    assert decision.needs_transform is True
    text = emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path).read_text(encoding="utf-8")
    assert "transform:" in text
    assert "function: master_onb_ba.gold_xlsx_ba_master_selected" in text


def test_gold_yml_source_wiring_to_bronze_source(tmp_path):
    """gold_xlsx_ba_master_selected reads bronze_xlsx_ba_master (a source entry, so layer=bronze)."""
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path).read_text(encoding="utf-8")
    assert "kind: table" in text
    assert "name: bronze_xlsx_ba_master" in text
    assert "layer: bronze" in text


def test_gold_yml_why_transform_comment_present(tmp_path):
    """The 'WHY transform' comment must explain the Unique decision."""
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path).read_text(encoding="utf-8")
    assert "WHY transform" in text
    assert "Unique" in text


def test_gold_yml_confidence_comment_present(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    text = emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path).read_text(encoding="utf-8")
    assert "# CONFIDENCE:" in text


def test_gold_yml_path_is_correct(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    emit.emit_gold_yml(ctx, table, decision, "master_onb_ba", tmp_path)
    expected = tmp_path / "config" / "master_onb_ba" / "gold" / "gold_xlsx_ba_master_selected.yml"
    assert expected.exists()


# ---------------------------------------------------------------------------
# Task 7: @transform stubs + domain.yml imports
# ---------------------------------------------------------------------------


def test_transform_stub_created_for_unique_any_table(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    decisions = classify_all(ctx)
    stub_path, imports = emit.emit_transform_stubs(ctx, decisions, "master_onb_ba", tmp_path)

    assert stub_path.exists()
    text = stub_path.read_text(encoding="utf-8")

    # The registered decorator must use the full domain.table name
    assert '@transform("master_onb_ba.gold_xlsx_ba_master_selected")' in text
    # The Python function should exist
    assert "def gold_xlsx_ba_master_selected" in text
    # Must import the registry decorator
    assert "from datalab.framework.registry import transform" in text


def test_transform_stub_imports_list_has_module(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    decisions = classify_all(ctx)
    _, imports = emit.emit_transform_stubs(ctx, decisions, "master_onb_ba", tmp_path)
    assert imports == ["master_onb_ba_etl.transforms.master_onb_ba"]


def test_pure_yaml_table_produces_no_transform_stub(tmp_path):
    """A table with only Select+Filter must produce no @transform stub and no imports."""
    ctx = _load_silver_ctx()
    decisions = classify_all(ctx)
    stub_path, imports = emit.emit_transform_stubs(ctx, decisions, "silver_test", tmp_path)
    assert imports == []
    text = stub_path.read_text(encoding="utf-8")
    # The decorator @transform("...") must not appear (the docstring comment is ok)
    import re
    assert not re.search(r'@transform\(', text)


def test_domain_yml_imports_updated_after_stubs(tmp_path):
    """update_domain_yml_imports must replace the placeholder with the real module list."""
    ctx = model.load_context(EXAMPLE_CONTEXT)
    decisions = classify_all(ctx)
    # Write domain.yml with placeholder first
    emit.emit_domain_yml("master_onb_ba", tmp_path)
    domain_yml = tmp_path / "config" / "master_onb_ba" / "domain.yml"

    assert "imports: []" in domain_yml.read_text(encoding="utf-8")

    _, imports = emit.emit_transform_stubs(ctx, decisions, "master_onb_ba", tmp_path)
    emit.update_domain_yml_imports(domain_yml, imports)

    updated = domain_yml.read_text(encoding="utf-8")
    assert "master_onb_ba_etl.transforms.master_onb_ba" in updated
    assert "imports: []" not in updated
