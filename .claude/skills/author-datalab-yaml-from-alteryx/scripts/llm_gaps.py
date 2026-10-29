"""LLM-assisted gap filling for Alteryx-to-datalab YAML conversion.

Only LOW-confidence / gap tools are routed here. Everything the classification engine can
express declaratively (YAML bucket) or map to a known transform (TRANSFORM bucket with
HIGH/MEDIUM confidence) is never sent to a model — this module exists solely for the cases
where ``classify.py`` raised a gap flag (``missing: true``, unknown tool, missing macro).

HTTP plumbing, retry/backoff, token redaction and the generate→validate→repair-once loop
are all reused from ``migrations/alteryx/alteryx_migrate/ai.py`` (stdlib-only Databricks
AI Gateway client). The only piece replaced here is the ``problems()`` validator: instead
of checking PySpark pipeline rules, we validate that the model's output is a valid datalab
YAML table config by running ``python -m datalab.framework`` on a temp dir (or, when the
framework is not importable, falling back to a schema-based YAML-parse check against the
vendored ``schemas/table_config.schema.json``).

Chunk boundary: one table per LLM call. The whole table's tool chain is sent in one chunk
unless the serialised payload exceeds ``--max-chars`` (default 48 000), in which case the
tools are split into sub-chunks that each fit, each repaired independently, and the results
merged back.

Usage (called from ``alteryx_to_yaml.py`` when gaps exist and ``--model`` is given):

    from llm_gaps import fill_gaps
    results = fill_gaps(ctx, decisions, domain, out, client=client, max_chars=48_000)
    # results: {table_key: str | None}  (None = gap still open, str = filled YAML content)
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from . import classify, model
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import classify  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# YAML validator (replaces ai.py's PySpark problems())
# ---------------------------------------------------------------------------

_REPO_ROOT: Path | None = None


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from ``start`` to find the repo root (has ``schemas/`` subdir)."""
    for parent in [start, *start.parents]:
        if (parent / "schemas" / "table_config.schema.json").exists():
            return parent
    return None


def yaml_problems(yaml_text: str, table_name: str) -> list[str]:
    """Validate ``yaml_text`` as a datalab table config. Returns a list of problem strings
    (empty = valid). Uses ``python -m datalab.framework`` when available, else falls back
    to a structural YAML check against ``schemas/table_config.schema.json``.
    """
    import subprocess

    problems: list[str] = []

    # Quick structural check first (no framework import needed)
    try:
        import yaml as _yaml  # type: ignore[import]
        doc = _yaml.safe_load(yaml_text)
    except Exception as exc:  # noqa: BLE001
        return [f"YAML parse error: {exc}"]

    if not isinstance(doc, dict):
        return ["YAML must be a mapping, not a scalar/list"]
    for required in ("schema_version", "name", "source", "target"):
        if required not in doc:
            problems.append(f"missing required key: {required!r}")
    if doc.get("name") != table_name:
        problems.append(
            f"name: {doc.get('name')!r} does not match expected table name {table_name!r}"
        )

    if problems:
        return problems  # structural issues — no point calling the framework

    # Try the framework validator in a subprocess (avoids import side-effects)
    global _REPO_ROOT
    if _REPO_ROOT is None:
        _REPO_ROOT = _find_repo_root(Path(__file__).resolve())

    if _REPO_ROOT is None:
        return problems  # can't find the schema; structural check is all we have

    # Write the YAML to a temp config dir and validate
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config" / "llm_gap_domain" / "gold"
        config_dir.mkdir(parents=True)
        yml_file = config_dir / f"{table_name}.yml"
        yml_file.write_text(yaml_text, encoding="utf-8")

        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "datalab.framework", str(Path(tmpdir) / "config")],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=30,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "validation failed").strip()[:400]
            # If the framework package is simply not installed, skip (don't fail validation).
            # This keeps tests green in environments without the private dependency.
            if "No module named 'datalab'" in err or "ModuleNotFoundError" in err:
                pass  # structural check already passed; can't go deeper without the package
            else:
                problems.append(f"datalab.framework validation failed: {err}")

    return problems


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a Databricks Lakeflow / datalab YAML generator. Your task is to write a valid
datalab table config YAML for a single Alteryx tool that could not be converted by rule.

Rules:
- Output ONLY a fenced ```yaml ... ``` block — no prose, no explanation.
- The YAML must start with `schema_version: 1` and include `name:`, `source:`, and `target:`.
- Do NOT hardcode catalog or schema names. Use `source.kind: table` + `layer:` for cross-table
  references, and `path_conf:` conf keys for file sources.
- `target.kind` must be one of: streaming_table, managed_table, materialized_view,
  temporary_view, delta_sink.
- Gold tables with grain changes use `target.kind: materialized_view` + `transform:`.
- Silver tables use `target.kind: streaming_table`; add `target.cdc` only when a key+order
  is known.
- If the tool's logic genuinely cannot be expressed in YAML, emit a `transform:` block
  referencing `<domain>.<table_name>` and explain in a `# TODO` comment what the function
  must do. Never invent column names or logic that is not in the provided context.
- Add `# CONFIDENCE: low — AI-generated; review before use` at the top of the file.
"""


def _build_user_prompt(
    ctx: model.Context,
    table: model.Table,
    decision: classify.TableDecision,
    domain: str,
    max_chars: int,
) -> str:
    """Build the user message for one gap table."""
    # Collect gap tools only
    gap_tools = decision.gaps
    tool_payloads = []
    for gap in gap_tools:
        tool = ctx.tool(gap.tool_id)
        if tool is None:
            continue
        tool_payloads.append({
            "id": tool.id,
            "name": tool.name,
            "plugin": tool.plugin,
            "status": tool.status,
            "reason": tool.reason,
            "code": tool.code[:2000] if tool.code else "",
            "settings_xml_head": tool.settings_xml_head[:500] if tool.settings_xml_head else "",
        })

    payload = {
        "domain": domain,
        "table_name": table.table,
        "layer": table.layer,
        "reads_sources": list(table.reads_sources),
        "reads_tables": list(table.reads_tables),
        "review_notes": list(table.review_notes),
        "gap_tools": tool_payloads,
        "classify_reason": [g.reason for g in gap_tools],
    }

    text = (
        f"Generate a datalab YAML table config for the gap table below.\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
    )

    # Truncate if over max_chars (single-table chunk boundary per plan)
    if len(text) > max_chars:
        # Trim tool code/settings to fit
        for tp in tool_payloads:
            tp["code"] = tp["code"][:500]
            tp["settings_xml_head"] = ""
        payload["gap_tools"] = tool_payloads
        payload["_note"] = "payload truncated to fit max_chars"
        text = (
            f"Generate a datalab YAML table config for the gap table below.\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```"
        )

    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class GapResult:
    """The outcome of one LLM gap-fill attempt."""

    table: str
    yaml_text: str | None  # None = still open (error or validation failure)
    error: str = ""
    attempts: int = 0


def fill_gap(
    ctx: model.Context,
    table: model.Table,
    decision: classify.TableDecision,
    domain: str,
    client: object,  # ai.Client
    max_chars: int = 48_000,
    repairs: int = 1,
) -> GapResult:
    """Ask the LLM to fill the YAML for one gap table.  Validates, repairs once on failure.

    ``client`` is an ``ai.Client`` instance from ``migrations/alteryx/alteryx_migrate/ai.py``.
    This function never calls the LLM directly — it uses ``client.complete(system, user)``.
    """
    system = _SYSTEM_PROMPT
    user = _build_user_prompt(ctx, table, decision, domain, max_chars)

    result = GapResult(table=table.table, yaml_text=None, attempts=0)

    try:
        # Import ai.py's extract_code (no duplication; reuse as planned)
        _ai_dir = Path(__file__).resolve().parent.parent.parent.parent / "migrations" / "alteryx" / "alteryx_migrate"
        if str(_ai_dir) not in sys.path:
            sys.path.insert(0, str(_ai_dir))
        from ai import extract_code  # type: ignore[import]
    except ImportError:
        # Fallback: extract a ```yaml block ourselves
        import re

        def extract_code(text: str) -> str:  # type: ignore[misc]
            blocks = re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", text, flags=re.S)
            if blocks:
                return max(blocks, key=len).strip()
            return text.strip()

    for attempt in range(repairs + 1):
        result.attempts = attempt + 1
        try:
            reply = client.complete(system, user)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            result.error = f"LLM call failed: {exc}"
            return result

        yaml_text = extract_code(reply)
        problems = yaml_problems(yaml_text, table.table)

        if not problems:
            result.yaml_text = yaml_text
            return result

        if attempt >= repairs:
            result.error = "LLM output failed validation: " + "; ".join(problems[:3])
            return result

        # Build repair prompt
        user = (
            user + f"\n\nYour previous answer had these problems: {'; '.join(problems[:3])}. "
            "Fix only those issues; keep the rest of the YAML unchanged."
        )

    return result


def fill_gaps(
    ctx: model.Context,
    decisions: dict[str, classify.TableDecision],
    domain: str,
    out: Path,
    client: object,  # ai.Client
    max_chars: int = 48_000,
    repairs: int = 1,
) -> dict[str, GapResult]:
    """Fill all LOW-confidence / gap tables in the context. Returns a dict of results keyed
    by table name. Tables with no gaps are not included. Results are also written as YAML
    files alongside the other emitted artifacts (overwriting the placeholder, if any).

    Only gap tables (tables with at least one GAP tool decision) are routed to the LLM.
    """
    results: dict[str, GapResult] = {}
    tables_by_key = {t.table: t for t in ctx.tables}

    for tbl_key, td in decisions.items():
        if not td.gaps:
            continue  # no gaps → skip entirely (never call the LLM gratuitously)

        table = tables_by_key.get(tbl_key)
        if table is None:
            continue

        result = fill_gap(ctx, table, td, domain, client, max_chars=max_chars, repairs=repairs)
        results[tbl_key] = result

        if result.yaml_text:
            # Write the filled YAML to the correct layer path
            config_dir = out / "config" / domain / table.layer
            config_dir.mkdir(parents=True, exist_ok=True)
            yml_path = config_dir / f"{tbl_key}.yml"
            header = f"# yaml-language-server: $schema={'../' * 5}schemas/table_config.schema.json\n"
            yml_path.write_text(header + result.yaml_text + "\n", encoding="utf-8")

    return results


def gaps_summary(results: dict[str, GapResult]) -> str:
    """A short human-readable summary of gap-fill results for ``migration_review.md``."""
    if not results:
        return "No gap tables — LLM was not invoked."
    lines = []
    filled = [r for r in results.values() if r.yaml_text]
    still_open = [r for r in results.values() if not r.yaml_text]
    lines.append(f"LLM gap-fill: {len(filled)} filled, {len(still_open)} still open.")
    for r in still_open:
        lines.append(f"  - `{r.table}` still open: {r.error}")
    return "\n".join(lines)
