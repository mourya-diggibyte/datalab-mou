"""Task 9: LLM gap-filling — yaml_problems validator + fill_gap routing logic."""

from pathlib import Path
from unittest.mock import MagicMock

import model
from classify import classify_all, classify_table
from llm_gaps import GapResult, fill_gap, fill_gaps, gaps_summary, yaml_problems

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _gap_ctx():
    return model.load_context(FIXTURES / "gap_context.json")


# ---------------------------------------------------------------------------
# yaml_problems validator
# ---------------------------------------------------------------------------


def test_valid_minimal_yaml_has_no_problems():
    yaml = """\
schema_version: 1
name: gold_complex
comment: test
source:
  kind: table
  name: bronze_raw
  layer: bronze
target:
  kind: materialized_view
"""
    assert yaml_problems(yaml, "gold_complex") == []


def test_missing_required_key_is_a_problem():
    yaml = """\
schema_version: 1
name: gold_complex
source:
  kind: table
  name: bronze_raw
  layer: bronze
"""
    problems = yaml_problems(yaml, "gold_complex")
    assert any("target" in p for p in problems)


def test_wrong_name_is_a_problem():
    yaml = """\
schema_version: 1
name: wrong_name
source:
  kind: table
  name: bronze_raw
  layer: bronze
target:
  kind: materialized_view
"""
    problems = yaml_problems(yaml, "gold_complex")
    assert any("gold_complex" in p for p in problems)


def test_yaml_parse_error_is_reported():
    problems = yaml_problems("not: valid: yaml: :\n  - broken", "gold_complex")
    # either YAML parse error or missing keys — either way, non-empty
    assert problems


# ---------------------------------------------------------------------------
# fill_gap — routing and validation
# ---------------------------------------------------------------------------

_VALID_YAML = """\
# CONFIDENCE: low — AI-generated; review before use
schema_version: 1
name: gold_complex
comment: "AI-filled gap table"
source:
  kind: table
  name: bronze_raw
  layer: bronze
transform:
  - function: gap_test.gold_complex
target:
  kind: materialized_view
"""


def test_fill_gap_accepts_valid_llm_reply(tmp_path):
    ctx = _gap_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)
    assert decision.gaps  # SpatialMatch is a gap

    client = MagicMock()
    client.complete.return_value = f"```yaml\n{_VALID_YAML}```"

    result = fill_gap(ctx, table, decision, "gap_test", client, max_chars=48_000)
    assert result.yaml_text is not None
    assert result.error == ""
    assert result.attempts == 1


def test_fill_gap_repairs_once_on_validation_failure(tmp_path):
    """First reply has wrong name; second reply is valid. Should succeed in 2 attempts."""
    ctx = _gap_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)

    bad_yaml = _VALID_YAML.replace("name: gold_complex", "name: wrong_name")
    client = MagicMock()
    client.complete.side_effect = [
        f"```yaml\n{bad_yaml}```",
        f"```yaml\n{_VALID_YAML}```",
    ]

    result = fill_gap(ctx, table, decision, "gap_test", client, max_chars=48_000, repairs=1)
    assert result.yaml_text is not None
    assert result.attempts == 2


def test_fill_gap_returns_error_when_llm_raises(tmp_path):
    ctx = _gap_ctx()
    table = ctx.tables[0]
    decision = classify_table(ctx, table)

    client = MagicMock()
    client.complete.side_effect = Exception("connection refused")

    result = fill_gap(ctx, table, decision, "gap_test", client)
    assert result.yaml_text is None
    assert "connection refused" in result.error


def test_fill_gaps_skips_tables_without_gaps(tmp_path):
    """fill_gaps must NOT call the LLM for tables that have no gap tools."""
    from conftest import EXAMPLE_CONTEXT
    ctx = model.load_context(EXAMPLE_CONTEXT)
    decisions = classify_all(ctx)

    client = MagicMock()
    results = fill_gaps(ctx, decisions, "master_onb_ba", tmp_path, client)

    # Master_ONB_BA has no gap tools (only missing macro, not missing=True tools)
    assert results == {}
    client.complete.assert_not_called()


def test_fill_gaps_writes_yaml_file_on_success(tmp_path):
    ctx = _gap_ctx()
    decisions = classify_all(ctx)

    client = MagicMock()
    client.complete.return_value = f"```yaml\n{_VALID_YAML}```"

    results = fill_gaps(ctx, decisions, "gap_test", tmp_path, client)

    assert "gold_complex" in results
    assert results["gold_complex"].yaml_text is not None
    expected = tmp_path / "config" / "gap_test" / "gold" / "gold_complex.yml"
    assert expected.exists()


def test_gaps_summary_no_gaps():
    assert "not invoked" in gaps_summary({})


def test_gaps_summary_mixed():
    results = {
        "table_a": GapResult(table="table_a", yaml_text="schema_version: 1"),
        "table_b": GapResult(table="table_b", yaml_text=None, error="timeout"),
    }
    summary = gaps_summary(results)
    assert "1 filled" in summary
    assert "1 still open" in summary
    assert "table_b" in summary
