# Migration review — master_onb_ba

| Field | Value |
|---|---|
| Workflow | `Master_ONB_BA (1).yxmd` |
| SHA-256 | `8f35b5c3cea42f33…` |
| Domain | `master_onb_ba` |
| Generated | 2026-10-28 16:20 UTC |
| Output tables | 1 |
| Source tables | 1 |
| @transform functions | 1 |
| Gap tools (route to LLM/review) | 0 |
| Dropped tools | 0 |
| Low-confidence tables | 0 |
| Medium-confidence tables | 1 |
| Macros missing | GoogleSheetsInput.yxmc |

## Dependency order

1. `bronze_xlsx_ba_master` (source)
2. `gold_xlsx_ba_master_selected` (gold)

## Tables

| Table | Layer | File | Confidence | Transform | Notes |
|---|---|---|---|---|---|
| `gold_xlsx_ba_master_selected` | gold | `config/master_onb_ba/gold/gold_xlsx_ba_master_selected.yml` | medium | yes Unique | Unique (keep-first): user accepted that any matching row may be kept (no order key) |

## Bronze sources

| Source key | Alteryx source file | Macro missing | Bronze file |
|---|---|---|---|
| `bronze_xlsx_ba_master` | GL Grouping - CSB original.xlsx BA Master | yes | `config/master_onb_ba/bronze/bronze_xlsx_ba_master.yml` |

## Needs your attention

- **MISSING MACRO `GoogleSheetsInput.yxmc`** — the bronze source YAML has a placeholder `path_conf`; set the real landing path conf key before running.
- **Unique(any) in `gold_xlsx_ba_master_selected`** — `dropDuplicates` may keep a different row than Alteryx did (no stable order key). If row identity matters, add a `sequence_by` key and switch to `target.cdc`.
- **MEDIUM confidence** `gold_xlsx_ba_master_selected` — [Unique]: Unique keeps 'any' matching row (no order key, decisions[].mode=any) — dropDuplicates, NOT cdc (cdc would pick a different, order-dependent survivor)
- **MEDIUM confidence** `gold_xlsx_ba_master_selected` — [Formula]: Formula assigns a literal/current_timestamp/cast — a derived column

## Dropped on purpose

- (no table) — [6] `Browse`: no effect on the data (not converted on purpose)

