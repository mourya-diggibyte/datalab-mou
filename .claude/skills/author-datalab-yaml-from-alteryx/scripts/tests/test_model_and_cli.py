"""Task 1: parsing conversion_context.json + the CLI's dry-run plan."""

from pathlib import Path

import model
from alteryx_to_yaml import dry_run_report, load, planned_files, slug
from conftest import EXAMPLE_CONTEXT


def test_slug_cleans_workflow_filenames():
    assert slug("Master_ONB_BA (1).yxmd") == "master_onb_ba"
    assert slug("simple.yxmd") == "simple"
    assert slug("2024_report.yxmd") == "d_2024_report"
    assert slug("***.yxmd") == "workflow"


def test_load_context_parses_the_worked_example():
    ctx = model.load_context(EXAMPLE_CONTEXT)
    assert ctx.workflow == "Master_ONB_BA (1).yxmd"
    assert ctx.tool_count == 6
    assert len(ctx.tools) == 6
    assert len(ctx.sources) == 1
    assert len(ctx.tables) == 1
    assert ctx.macros_missing == ("GoogleSheetsInput.yxmc",)

    table = ctx.tables[0]
    assert table.table == "gold_xlsx_ba_master_selected"
    assert table.layer == "gold"
    assert table.reads_sources == ("bronze_xlsx_ba_master",)
    assert table.tool_ids == (9, 5, 2)

    # tools_for() resolves ids to Tool records, in the table's build order.
    chain = ctx.tools_for(table)
    assert [t.name for t in chain] == ["Unique", "Formula", "Select"]
    assert all(t.status == "converted by rule" for t in chain)

    source_tool = ctx.tool(11)
    assert source_tool is not None
    assert source_tool.is_source

    browse_tool = ctx.tool(6)
    assert browse_tool is not None
    assert browse_tool.is_dropped

    output_tool = ctx.tool(12)
    assert output_tool is not None
    assert output_tool.is_output_tool
    # settings_xml is capped so a huge embedded tree never blows up a prompt/context.
    assert len(output_tool.settings_xml_head) <= model._SETTINGS_XML_HEAD


def test_load_rejects_an_unexpected_layer(tmp_path: Path):
    import json

    bad = tmp_path / "bad_context.json"
    bad.write_text(
        json.dumps(
            {
                "workflow": "x.yxmd",
                "sha256": "",
                "tool_count": 0,
                "sources": [],
                "tables": [{"table": "t", "layer": "platinum", "tools": []}],
                "tools": [],
                "missing": [],
                "macros_missing": [],
                "decisions": {},
                "decided_for_you": {},
            }
        ),
        encoding="utf-8",
    )

    class Args:
        context = str(bad)
        notebooks_dir = None

    try:
        load(Args())
    except SystemExit as exc:
        assert "platinum" in str(exc)
    else:
        raise AssertionError("expected SystemExit for an unexpected layer")


def test_planned_files_and_dry_run_report_match_the_example():
    ctx = model.load_context(EXAMPLE_CONTEXT)
    domain = "master_onb_ba"
    out = Path("projects") / domain

    files = planned_files(ctx, domain, out)
    posix = {f.as_posix() for f in files}
    assert "projects/master_onb_ba/config/master_onb_ba/domain.yml" in posix
    assert (
        "projects/master_onb_ba/config/master_onb_ba/gold/gold_xlsx_ba_master_selected.yml"
        in posix
    )
    assert "projects/master_onb_ba/src/master_onb_ba_etl/transforms/master_onb_ba.py" in posix
    assert "projects/master_onb_ba/classification.json" in posix
    assert "projects/master_onb_ba/migration_review.md" in posix

    report = dry_run_report(ctx, domain, out)
    assert "tables   : 1   sources: 1   tools: 6" in report
    assert "GoogleSheetsInput.yxmc" in report
    assert "gold_xlsx_ba_master_selected" in report
    # the dependency graph (Task 2) orders the bronze source before the gold table.
    assert report.index("bronze_xlsx_ba_master") < report.index("gold_xlsx_ba_master_selected")
