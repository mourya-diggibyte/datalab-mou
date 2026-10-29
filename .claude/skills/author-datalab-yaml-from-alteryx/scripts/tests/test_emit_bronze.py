"""Task 4: bronze emission + domain.yml skeleton."""

import emit
import model
from conftest import EXAMPLE_CONTEXT


def test_domain_yml_has_correct_shape(tmp_path):
    domain = "master_onb_ba"
    p = emit.emit_domain_yml(domain, tmp_path)

    assert p.exists()
    text = p.read_text(encoding="utf-8")

    # Schema header at depth 4 (projects/<domain>/config/<domain>/domain.yml -> repo root)
    assert "# yaml-language-server: $schema=../../../../schemas/domain_config.schema.json" in text

    # Conf keys, never literal catalog/schema
    assert "catalog_conf: datalab.catalog" in text
    assert "schema_conf: datalab.bronze_schema" in text
    assert "schema_conf: datalab.silver_schema" in text
    assert "schema_conf: datalab.gold_schema" in text

    # Domain name
    assert "domain: master_onb_ba" in text

    # Imports placeholder
    assert "imports:" in text

    # No literal catalog paths
    assert "." not in text.split("catalog_conf:")[1].split("\n")[0].replace("datalab.catalog", "")


def test_domain_yml_is_at_correct_path(tmp_path):
    emit.emit_domain_yml("master_onb_ba", tmp_path)
    expected = tmp_path / "config" / "master_onb_ba" / "domain.yml"
    assert expected.exists()


def test_bronze_yml_emitted_for_source_entry(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    p = emit.emit_bronze_yml(ctx, "master_onb_ba", "bronze_xlsx_ba_master", tmp_path)

    assert p.exists()
    text = p.read_text(encoding="utf-8")

    # Schema header at depth 5
    assert "# yaml-language-server: $schema=../../../../../schemas/table_config.schema.json" in text

    # Core fields
    assert "name: bronze_xlsx_ba_master" in text
    assert "kind: cloud_files" in text
    assert "kind: streaming_table" in text
    assert "add_lineage: true" in text

    # Conf key, never a literal path
    assert "path_conf: datalab.bronze_xlsx_ba_master_landing_path" in text
    assert "catalog" not in text.split("path_conf:")[1].split("\n")[0]


def test_bronze_yml_has_missing_macro_annotation(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    text = emit.emit_bronze_yml(
        ctx, "master_onb_ba", "bronze_xlsx_ba_master", tmp_path
    ).read_text(encoding="utf-8")

    # macros_missing = ["GoogleSheetsInput.yxmc"] in the real example
    assert "GoogleSheetsInput.yxmc" in text
    assert "CONFIDENCE: low" in text or "MISSING MACRO" in text


def test_bronze_yml_path_is_correct(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    emit.emit_bronze_yml(ctx, "master_onb_ba", "bronze_xlsx_ba_master", tmp_path)
    expected = tmp_path / "config" / "master_onb_ba" / "bronze" / "bronze_xlsx_ba_master.yml"
    assert expected.exists()


def test_no_literal_catalog_in_any_emitted_file(tmp_path):
    ctx = model.load_context(EXAMPLE_CONTEXT)
    domain = "master_onb_ba"

    emit.emit_domain_yml(domain, tmp_path)
    emit.emit_bronze_yml(ctx, domain, "bronze_xlsx_ba_master", tmp_path)

    for yml in tmp_path.rglob("*.yml"):
        text = yml.read_text(encoding="utf-8")
        # Must not contain a literal layer-namespace reference like ``mycat.bronze``
        import re
        bad = re.search(r'(?<!layer: )\b\w+\.(bronze|silver|gold)\b', text)
        assert bad is None, f"{yml.name} contains a literal catalog path: '{bad.group()}'"
