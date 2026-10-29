# Layer playbook — bronze / silver / gold

How the skill handles each medallion layer. One worked example per layer.

## Bronze — ingestion

**When:** the `sources[]` entry in `conversion_context.json`. Every source becomes one bronze YAML.

**YAML shape:**
```yaml
# yaml-language-server: $schema=../../../../../schemas/table_config.schema.json
schema_version: 1
name: bronze_xlsx_ba_master
comment: "Raw ingestion — schema preserved, no column mapping at bronze."
source:
  kind: cloud_files
  path_conf: datalab.bronze_xlsx_ba_master_landing_path  # FIXME: set conf key
  format: csv  # Alteryx source was .xlsx — convert to CSV/Parquet for Auto Loader
  options:
    cloudFiles.inferColumnTypes: "true"
    cloudFiles.schemaEvolutionMode: addNewColumns
  add_lineage: true  # adds _ingest_ts + _source_file
target:
  kind: streaming_table
```

**Rules:**
- No `columns:` at bronze — schema evolution on.
- `path_conf` is ALWAYS a conf key (e.g. `datalab.landing_path`), never a literal path.
- If the source macro is missing, add `# CONFIDENCE: low — MISSING MACRO` and a `# FIXME`.
- `add_lineage: true` so downstream silver can use `_ingest_ts` for CDC `sequence_by`.

**Worked example:** `Master_ONB_BA` — `bronze_xlsx_ba_master` from Google Sheets (missing macro).

---

## Silver — record-level validation + optional CDC

**When:** a `tables[]` entry with `layer: silver`.

**YAML shape (Select + Filter, no transform):**
```yaml
schema_version: 1
name: silver_orders
comment: "Validated orders, current state per order_id (SCD1)."
source:
  kind: table
  name: bronze_orders   # reads_sources[0]
  layer: bronze
  as_stream: true
columns:
  - name: order_id
    type: long
    quality:
      - criticality: error
        check: {function: is_not_null}
  - name: customer_id
    type: long
  - name: total_amount
    source: total       # rename detected from .alias('total_amount')
    type: double
target:
  kind: streaming_table
  cdc:
    keys: ["order_id"]
    sequence_by: _ingest_ts
    scd_type: 1
```

**Rules:**
- `columns:` is derived from the Select tool's generated PySpark (cast+alias patterns).
- Quality rules are extracted from the Filter tool's `.isNotNull()` / `.isin()` patterns.
- `target.cdc` is added ONLY when a `Unique` tool has `decisions.mode == "first"/"last"`.
- A `Unique` with `mode == "any"` → `@transform: dropDuplicates` (never cdc — cdc picks
  a different, order-dependent row).
- A pure Select+Filter table → `needs_transform: False` → NO `transform:` block emitted.
- Columns missing from the Select list (runtime-conditional `*([...] if ... in ...)`) get
  a `# conditional — field may be absent at runtime` note.

**When is a transform added at silver?**
Only when a silver table contains a Summarize, Join, Multi-Row Formula, etc. (rare).
In that case `transform: [{function: <domain>.<table>}]` is added and a stub is generated.

---

## Gold — grain-changing aggregate / dedupe

**When:** a `tables[]` entry with `layer: gold`.

**YAML shape (Unique(any) → @transform):**
```yaml
# CONFIDENCE: medium — Unique(any) keeps an arbitrary row
# WHY transform: [Unique] dropDuplicates, NOT cdc (cdc would pick a different survivor)
schema_version: 1
name: gold_xlsx_ba_master_selected
comment: "Gold layer: grain-changing dedupe."
source:
  kind: table
  name: bronze_xlsx_ba_master   # reads_sources[0] → layer: bronze
  layer: bronze
transform:
  - function: master_onb_ba.gold_xlsx_ba_master_selected
    # @transform: dropDuplicates (any-row semantics)
target:
  kind: materialized_view
```

**Rules:**
- `source.kind: table` always — gold reads a built table, never a raw file.
- `target.kind: materialized_view` always at gold.
- `transform:` is REQUIRED at gold (grain changes, joins, window functions, deduplication).
  A gold table without a transform is flagged with a `# CONFIDENCE: medium — review` comment.
- The `function:` name is `<domain>.<table_name>`, matching the Python stub's `@transform` decorator.
- `# WHY transform` comment documents exactly which tool forced the decision.

**Worked example:** `gold_xlsx_ba_master_selected` — Unique(any) + Formula + Select
on top of `bronze_xlsx_ba_master`. The whole chain goes into the `@transform` stub.

---

## Source layer resolution

When a gold/silver table's `reads_sources[0]` points at an entry in `sources[]` (a declared
source that has a bronze YAML), the layer is always `bronze`.
When it points at another `tables[]` entry, the layer is that table's `.layer`.

```
reads_sources -> sources[].table_key  -> layer: bronze
reads_tables  -> tables[].table       -> layer: tables[].layer
```
