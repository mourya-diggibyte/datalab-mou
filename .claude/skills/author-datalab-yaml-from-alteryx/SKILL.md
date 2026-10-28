---
name: author-datalab-yaml-from-alteryx
description: Use when you need to turn Alteryx-migration output (conversion_context.json + notebooks) into datalab YAML artifacts (domain.yml, bronze/silver/gold table configs, @transform stubs, migration_review.md). Handles classification of each Alteryx tool into pure YAML vs @transform vs gap, confidence scoring, dependency ordering, and optional LLM gap-filling.
---

# `author-datalab-yaml-from-alteryx` skill

Reads the structured output of `migrations/alteryx/` and emits production-ready
datalab YAML artifacts under `projects/<domain>/config/`.

## When to use

- After running the Alteryx migration tool and getting a `conversion_context.json`
- To plug a migrated workflow into the config-driven Databricks framework
- To generate `@transform` stubs, `domain.yml`, and a `migration_review.md` hand-off

## Quick start

```bash
# Run from the repo root (Git Bash / Python 3.12+)
uv run --no-project python .agents/skills/author-datalab-yaml-from-alteryx/scripts/alteryx_to_yaml.py \
    --context "migrations/alteryx/out/My_Workflow/conversion_context.json" \
    --out projects/my_workflow \
    --domain my_workflow \
    --catalog-conf datalab.catalog

# Dry-run first to see the plan without writing files
... --dry-run
```

Outputs (under `--out`):
- `config/<domain>/domain.yml` — catalog/schema conf keys + imports
- `config/<domain>/bronze/<source>.yml` — one per source entry
- `config/<domain>/silver/<table>.yml` — columns + quality + optional cdc
- `config/<domain>/gold/<table>.yml` — transform + materialized_view
- `src/<domain>_etl/transforms/<domain>.py` — `@transform` stubs (draft)
- `classification.json` — per-table/tool decisions + dep order
- `migration_review.md` — human hand-off (open this first)

After generation: open `migration_review.md` and fix every item in **Needs your attention**
before running `make validate-configs`.

## Layer rules

| Layer | Source kind | Target kind | Transform? |
|---|---|---|---|
| **Bronze** | `cloud_files` or `files` | `streaming_table` | never |
| **Silver** | `table` + `as_stream: true` | `streaming_table` | only if grain changes |
| **Gold** | `table` | `materialized_view` | required (grain-changing) |

See `references/layer-playbook.md` for worked examples.

## Classification engine

Each Alteryx tool is classified into one bucket:

| Bucket | What it means | Output |
|---|---|---|
| `yaml` | Declarative: Select → `columns:`, Filter → `quality:` | Emitted directly |
| `transform` | Grain change / multi-input / orderless dedupe | `@transform` stub |
| `drop` | No effect, Browse, dead branch | Comment only |
| `gap` | `missing: true`, unknown tool, missing macro | Placeholder + LLM/review |

The table's confidence = weakest tool's confidence. Any gap forces LOW.

See `references/tool-mapping.md` for the full mapping table.

## `@transform` — only when YAML cannot express it

A `@transform` is **never** emitted unless the tool genuinely requires it:

- Select rename/cast → `columns:` with `name`/`source`/`type` ✓
- Filter isNotNull → `quality:` rule with `criticality: error` ✓
- Formula with `current_timestamp`/`.cast(` → derived `columns:` entry ✓
- Unique with `decisions.mode == "any"` → `dropDuplicates` transform (NOT cdc) ✗
- Summarize / Join / Union / window → transform ✗

See `references/gap-handling.md` for the decision tree.

## Confidence scoring

| Level | Meaning | Action |
|---|---|---|
| `high` | Converted by rule, known tool, no review notes | Auto-emit |
| `medium` | Sound mapping but judgment call (Formula→column, Unique semantics, review_notes) | Auto-emit + `# CONFIDENCE: medium` comment |
| `low` | `missing: true`, missing macro, unknown tool, cycle | Placeholder; route to LLM or human |

## LLM gap-filling (optional)

When `--model` is given and gap tools exist, `llm_gaps.fill_gaps()` is called for each
LOW-confidence table. The LLM reply is validated with `yaml_problems()` (structural check
+ `datalab.framework` when available), repaired once on failure, and written alongside the
other YAML. Still-open gaps are listed in `migration_review.md`.

```bash
# With LLM gap-filling (requires DATABRICKS_HOST + DATABRICKS_TOKEN)
... --model databricks-meta-llama-3-3-70b-instruct --max-chars 48000
```

## Running the tests

The skill's own tests live under `scripts/tests/` and run independently:

```bash
# From the repo root
uv run --no-project --with "pytest,pyyaml" \
    python -m pytest ".agents/skills/author-datalab-yaml-from-alteryx/scripts/tests" -q
```

These are NOT in the global `testpaths` (that would cause pytest to collect them twice
after `make sync-skills` mirrors them into `.claude/skills/`). Run explicitly as above.

## Self-checks enforced

1. **No literal catalog paths** — every YAML is checked by `_assert_no_literal_catalog()`
   before writing; a `catalog.bronze`-style string raises immediately.
2. **Schema headers** — every emitted file starts with
   `# yaml-language-server: $schema=<rel>/schemas/table_config.schema.json`
   at the correct relative depth.
3. **Conf keys only** — catalog/schema values go through `catalog_conf`/`schema_conf` keys
   resolved by bundle variables, never hardcoded.

## Files

```
.agents/skills/author-datalab-yaml-from-alteryx/
  SKILL.md                          ← this file
  references/
    tool-mapping.md                 ← Alteryx tool → datalab construct catalog
    layer-playbook.md               ← bronze/silver/gold rules + worked examples
    gap-handling.md                 ← classification decision tree + LLM protocol
  scripts/
    model.py                        ← typed parser for conversion_context.json
    classify.py                     ← classification engine + confidence scoring
    depgraph.py                     ← table dependency DAG
    emit.py                         ← YAML / domain.yml / transform / review emitters
    llm_gaps.py                     ← LLM gap-fill wrapper + YAML validator
    alteryx_to_yaml.py              ← CLI entrypoint
    tests/
      conftest.py
      test_model_and_cli.py
      test_depgraph.py
      test_classify.py
      test_emit_bronze.py
      test_emit_silver_gold_transform.py
      test_llm_gaps.py
      fixtures/
        silver_context.json
        gap_context.json
        cycle_context.json
        dangling_context.json
```
