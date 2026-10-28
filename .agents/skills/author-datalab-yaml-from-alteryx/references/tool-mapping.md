# Alteryx tool → datalab construct mapping

Complete catalog used by `classify.py`. Every tool the migration tool emits is in one row.

## Status vocabulary

| Status prefix | Meaning |
|---|---|
| `source` | Input tool; becomes the table's `source:` |
| `output tool (...)` | Export sink; NOT a datalab target — record as `# EXPORT` comment |
| `converted by rule` | Tool was converted deterministically |
| `written by the model (AI-WRITTEN: review before use)` | `missing: true` — route to LLM |
| `NOT CONVERTED - ...` | `missing: true` — route to LLM |
| `no effect on the data (not converted on purpose)` | DROP — never emitted |
| `not needed for any output ...` | DROP — never emitted |
| `its result goes nowhere ...` | DROP — never emitted |

## Full tool mapping

| Alteryx tool | Condition | datalab construct | Bucket | Confidence |
|---|---|---|---|---|
| Any tool | `missing: true` | Gap placeholder | `gap` | `low` |
| Source tool | `status: source` | `source:` wiring | `wiring` | `high` |
| Output tool | `status: output tool (...)` | `# EXPORT (not wired)` | `drop` | `medium` |
| Browse | `status: no effect...` | Dropped | `drop` | `high` |
| Any | `status: not needed...` / `its result goes nowhere` | Dropped | `drop` | `high` |
| **Select** | All | `columns:` (name/source/type) | `yaml` | `high` |
| **Filter** | All | `quality:` rule (criticality + check) | `yaml` | `high` |
| **Formula** | `current_timestamp` / `.cast(` / `lit(` only | Derived `columns:` entry | `yaml` | `medium` |
| **Formula** | `f.when` / `f.col(` with `withColumn` | `@transform` | `transform` | `medium` |
| **Unique** | `decisions.mode == "any"` | `@transform: dropDuplicates` (NOT cdc) | `transform` | `medium` |
| **Unique** | `decisions.mode == "first"/"last"` | `target.cdc` (review) or `@transform: dedupe` | `transform` | `medium` |
| **Unique** | No recorded decision | Gap — cannot tell cdc vs dropDuplicates | `gap` | `low` |
| **Summarize** | All | `@transform` (grain change) | `transform` | `high` |
| **Cross Tab** | All | `@transform` (pivot) | `transform` | `high` |
| **Join** | All | `@transform` (multi-input) | `transform` | `high` |
| **Union** | All | `@transform` (multi-input) | `transform` | `high` |
| **Append Fields** | All | `@transform` (multi-input) | `transform` | `high` |
| **Multi-Row Formula** | All | `@transform` (window/row-order) | `transform` | `high` |
| **Running Total** | All | `@transform` (window) | `transform` | `high` |
| **Record ID** | All | `@transform` (sequence) | `transform` | `high` |
| **Generate Rows** | All | `@transform` (expand) | `transform` | `high` |
| **Sort** | All | `@transform` (order-sensitive) | `transform` | `high` |
| Unknown tool | `status: converted by rule` | `@transform` (review) | `transform` | `medium` |
| Unknown tool | Any other status | Gap | `gap` | `low` |

## Source kind selection (bronze layer)

| Alteryx source type | datalab `source.kind` | Notes |
|---|---|---|
| Excel / CSV file landing | `cloud_files` (Auto Loader) | Set `path_conf` to the landing volume key |
| Google Sheets (macro) | `cloud_files` + `# MISSING MACRO` | Macro missing → LOW confidence |
| Existing Delta table | `table` + `layer: bronze` | Already in Unity Catalog |
| SQL query | `sql` | Paste translated SQL into `query:` |

## Quality rule mapping (Filter → quality:)

| PySpark pattern | DQX check function | Criticality |
|---|---|---|
| `.isNotNull()` | `is_not_null` | `error` |
| `.isNull()` | `is_null` | `warn` |
| `.isin(...)` | `is_in_list` | `error` (review values) |
| `> 0` / `>= 0` | `is_not_less_than` | `warn` |
| Custom expression | Manual review | `warn` (medium confidence) |
