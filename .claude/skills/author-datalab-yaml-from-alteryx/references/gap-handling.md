# Gap handling — classification decision tree + LLM protocol

## What is a gap?

A tool is a gap when `classify.py` cannot confidently express its logic as YAML or a
`@transform` stub without guessing. This happens when:

1. `tool.missing == True` (`AI-WRITTEN` or `NOT CONVERTED` status)
2. The tool plugin is not in the known vocabulary and has no `converted by rule` status
3. A `Unique` tool has no recorded `decisions` / `decided_for_you` entry
4. A macro needed by a source tool is missing (`macros_missing[]`)

A gap forces the table's confidence to `LOW`. Gap tools are NEVER silently emitted as
working constructs — they are always flagged in the YAML as `# ALTERYX-GAP` placeholders
and listed in `migration_review.md` under **Needs your attention**.

## Decision tree

```
Tool arrives at classify_tool()
│
├── tool.missing == True  ──────────────────────────────────► GAP / low
│
├── tool.is_source  ─────────────────────────────────────────► WIRING / high
│
├── tool.is_output_tool  ────────────────────────────────────► DROP / medium
│                                                               (# EXPORT comment)
├── tool.is_dropped or DROP status prefix ───────────────────► DROP / high
│
├── tool.name == "Unique"
│     ├── decisions.mode == "any"  ───────────────────────────► TRANSFORM / medium
│     │   (dropDuplicates, NOT cdc)
│     ├── decisions.mode == "first"/"last"  ─────────────────► TRANSFORM / medium
│     │   (target.cdc or @transform: dedupe)
│     └── no recorded decision  ──────────────────────────────► GAP / low
│
├── tool.name == "Formula"
│     ├── code has current_timestamp/.cast(/lit( only  ─────── YAML / medium
│     └── code has f.when/f.col(+withColumn  ─────────────────► TRANSFORM / medium
│
├── tool.name in TRANSFORM_TOOL_NAMES  ──────────────────────► TRANSFORM / high
│   (Summarize, Join, Union, Sort, etc.)
│
├── tool.name in YAML_TOOL_NAMES  ───────────────────────────► YAML / high
│   (Select, Filter)
│
├── tool.status == "converted by rule" (unknown name)  ──────► TRANSFORM / medium
│
└── anything else  ───────────────────────────────────────────► GAP / low
```

## LLM protocol

When `--model <endpoint>` is given and gap tools exist, `llm_gaps.fill_gaps()` is called.

### What is sent

- Only the gap table's tools (not the whole workflow)
- Per-tool: `id`, `name`, `plugin`, `status`, `reason`, first 2000 chars of `code`,
  first 500 chars of `settings_xml_head`
- The table's `reads_sources`, `reads_tables`, `review_notes`
- The domain name and layer

### Chunk boundary

One LLM call per gap table. The payload is sent whole unless its JSON serialisation exceeds
`--max-chars` (default 48 000), in which case tool `code` and `settings_xml_head` are
truncated to 500 chars each to fit.

### Validation + repair

The LLM reply is validated by `yaml_problems()`:
1. Structural: parses as YAML, has required keys (`schema_version`, `name`, `source`, `target`), `name` matches the table name.
2. Framework: runs `python -m datalab.framework` on a temp dir (when the package is importable).

If problems are found, the model is asked to repair once (the problems are sent back in the
next turn). If still failing after one repair, the result is written as a `# STILL OPEN`
placeholder and listed in `migration_review.md`.

### What the LLM must NOT do

- Invent column names not in the provided context
- Hardcode catalog or schema names
- Write `collect()` / `toPandas()` / UDFs
- Write `import dlt` or `from pyspark.pipelines`
- Leave `TODO` / `FIXME` / `NotImplementedError` in the output

These are all enforced by `yaml_problems()` (and adapted from `ai.py`'s `problems()` for
the original Alteryx migration tool).

## Missing macro handling

A missing macro (`macros_missing[]` entry) means the source connector's Alteryx plugin
was not available during conversion. It is NOT routed to the LLM — it requires a human to:

1. Identify what format the source data actually lands in (CSV, Parquet, JSON, etc.)
2. Set the correct `path_conf` key for the landing volume
3. Confirm the `format:` field in the bronze YAML

The emitter writes `# CONFIDENCE: low — MISSING MACRO(S): <macro>` and a `# FIXME` on
`path_conf`. These appear in `migration_review.md` under **Needs your attention**.

## Output tools

Output tools (`big_query_output`, etc.) are NEVER emitted as datalab sinks. The datalab
table IS the sink; exporting data to an external system is a post-pipeline step. The
emitter records `# EXPORT (not wired): <dest>` so the reviewer knows to add an export
step if needed. These appear in `migration_review.md` under **Needs your attention**.
