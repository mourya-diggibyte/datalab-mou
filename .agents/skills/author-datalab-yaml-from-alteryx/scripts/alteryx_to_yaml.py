"""Generate datalab YAML artifacts from Alteryx-migration output.

Reads a ``conversion_context.json`` (the structured output of ``migrations/alteryx/``) and
emits one ``config/<domain>/<layer>/<table>.yml`` per output table, a ``domain.yml``, a
``@transform`` stub module, a machine-readable ``classification.json`` and a human-facing
``migration_review.md``. It NEVER calls an LLM itself: steps it cannot express declaratively
are emitted as clearly-commented placeholders and recorded for the agent (or ``llm_gaps.py``)
to fill.

Catalog and schema are never hardcoded: every location is a pipeline-conf KEY resolved per
environment (``datalab.catalog``, ``datalab.<layer>_schema``) — see ``docs/NAMING.md``.

Usage (run from the repo root; works on Windows via Git Bash or plain python):

    uv run --no-project python <this>/alteryx_to_yaml.py \
        --context "migrations/alteryx/out/Master_ONB_BA (1)/conversion_context.json" \
        --out projects/master_onb_ba [--domain master_onb_ba] [--dry-run]

This is Task 1 (skeleton + parse + dry-run plan). Emission, classification, the dependency
graph, review file and the LLM-gap path land in later tasks and are wired in here as they come.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Importable both as a module (tests) and as a script (``python alteryx_to_yaml.py``).
try:
    from . import classify, depgraph, emit, model
except ImportError:  # pragma: no cover - script invocation puts this dir on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import classify  # type: ignore[no-redef]
    import depgraph  # type: ignore[no-redef]
    import emit  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]

VALID_LAYERS = ("bronze", "silver", "gold")


def slug(name: str) -> str:
    """Turn a workflow filename into a domain/catalog identifier.

    ``"Master_ONB_BA (1).yxmd"`` -> ``"master_onb_ba"``. Lower-cased, non-alphanumerics
    collapsed to single underscores, leading digits prefixed with ``d_`` so the result is a
    valid identifier (datalab table/domain names match ``^[A-Za-z_][A-Za-z0-9_]*$``).
    """
    stem = Path(name).stem  # drop .yxmd
    stem = re.sub(r"\(.*?\)", "", stem)  # drop parenthetical copy markers like "(1)"
    out = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_").lower()
    out = re.sub(r"_+", "_", out)
    if not out:
        out = "workflow"
    if out[0].isdigit():
        out = "d_" + out
    return out


def _find_notebook_context(notebooks_dir: Path) -> model.Context:
    """Fallback when no JSON is present: reconstruct a minimal context from the notebooks.

    Intentionally lossy — only the ``# [id] Kind`` markers and layer filenames are recovered,
    with no per-tool status or edges. Flagged ``from_notebooks=True`` so the caller warns.
    Full reconstruction is a later task; Task 1 ships the stub so the CLI contract is stable.
    """
    raise SystemExit(
        "Notebook fallback is not implemented yet. Pass --context <conversion_context.json> "
        f"(looked in {notebooks_dir})."
    )


def load(args: argparse.Namespace) -> model.Context:
    if args.context:
        ctx = model.load_context(args.context)
    elif args.notebooks_dir:
        ctx = _find_notebook_context(Path(args.notebooks_dir))
    else:
        raise SystemExit("Give --context <conversion_context.json> (or --notebooks-dir).")
    bad = [t.layer for t in ctx.tables if t.layer not in VALID_LAYERS]
    if bad:
        raise SystemExit(f"Unexpected layer(s) {sorted(set(bad))}; expected {VALID_LAYERS}.")
    return ctx


def planned_files(ctx: model.Context, domain: str, out: Path) -> list[Path]:
    """The config/transform/report files this run WOULD write (no classification yet)."""
    config_root = out / "config" / domain
    files = [config_root / "domain.yml"]
    for tbl in ctx.tables:
        files.append(config_root / tbl.layer / f"{tbl.table}.yml")
    files.append(out / f"src/{domain}_etl/transforms/{domain}.py")
    files.append(out / "classification.json")
    files.append(out / "migration_review.md")
    return files


def dry_run_report(ctx: model.Context, domain: str, out: Path) -> str:
    lines = [
        f"workflow : {ctx.workflow}",
        f"sha256   : {ctx.sha256[:12]}",
        f"domain   : {domain}",
        f"out      : {out}",
        f"tables   : {len(ctx.tables)}   sources: {len(ctx.sources)}   tools: {len(ctx.tools)}",
    ]
    if ctx.macros_missing:
        lines.append(f"macros_missing: {', '.join(ctx.macros_missing)}")
    lines.append("")

    try:
        graph = depgraph.build(ctx)  # raises before anything is written
    except depgraph.GraphError as exc:
        raise SystemExit(f"dependency graph error: {exc}") from None
    tables_by_key = {t.table: t for t in ctx.tables}
    lines.append("Build order (sources then tables, upstream-first):")
    for key in graph.order:
        tbl = tables_by_key.get(key)
        layer = tbl.layer if tbl else "source"
        chain = (tbl.chain if tbl else "") or ", ".join(
            str(i) for i in (tbl.tool_ids if tbl else ())
        )
        lines.append(f"  [{layer:6}] {key}" + (f"   ({chain})" if chain else ""))
    if graph.fan_in:
        lines.append("")
        lines.append("Fan-in (2+ upstreams):")
        lines += [f"  {t} <- {', '.join(ups)}" for t, ups in graph.fan_in.items()]
    if graph.fan_out:
        lines.append("")
        lines.append("Fan-out (feeds 2+ tables):")
        lines += [f"  {k} -> {', '.join(ds)}" for k, ds in graph.fan_out.items()]

    lines.append("")
    lines.append("Planned files:")
    for f in planned_files(ctx, domain, out):
        lines.append(f"  {f.as_posix()}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alteryx_to_yaml",
        description="Generate datalab YAML artifacts from Alteryx-migration output.",
    )
    p.add_argument("--context", help="path to conversion_context.json (primary input)")
    p.add_argument("--notebooks-dir", help="dir with 0*_*.ipynb (fallback when --context absent)")
    p.add_argument("--domain", help="domain/catalog slug (default: slug of the workflow name)")
    p.add_argument("--out", help="output root, e.g. projects/<domain>")
    p.add_argument("--catalog-conf", default="datalab.catalog", help="catalog conf KEY")
    p.add_argument("--max-chars", type=int, default=48000, help="per-table LLM chunk threshold")
    p.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = load(args)
    domain = args.domain or slug(ctx.workflow)
    out = Path(args.out) if args.out else Path("projects") / domain
    catalog_conf = getattr(args, "catalog_conf", "datalab.catalog")

    if args.dry_run:
        print(dry_run_report(ctx, domain, out))
        return 0

    # Build dependency graph (fails fast before any file is written).
    try:
        graph = depgraph.build(ctx)
    except depgraph.GraphError as exc:
        raise SystemExit(f"dependency graph error: {exc}") from None

    # Classify every table.
    decisions = classify.classify_all(ctx)

    written: list[Path] = []

    # 1. domain.yml skeleton.
    p = emit.emit_domain_yml(domain, out, catalog_conf=catalog_conf)
    written.append(p)
    print(f"  wrote {p.as_posix()}")

    # 2. Bronze: emit one YAML per source entry (sources represent ingestion tables).
    for source in ctx.sources:
        p = emit.emit_bronze_yml(ctx, domain, source.table_key, out, catalog_conf=catalog_conf)
        written.append(p)
        print(f"  wrote {p.as_posix()}")

    # 3. Silver / Gold tables: emit in topological order.
    tables_by_key = {t.table: t for t in ctx.tables}
    for key in graph.order:
        tbl = tables_by_key.get(key)
        if tbl is None:
            continue  # source node, already handled above
        td = decisions[tbl.table]
        if tbl.layer == "silver":
            try:
                p = emit.emit_silver_yml(ctx, tbl, td, domain, out)
                written.append(p)
                print(f"  wrote {p.as_posix()}")
            except NotImplementedError as exc:
                print(f"  [skip] {exc}")
        elif tbl.layer == "gold":
            try:
                p = emit.emit_gold_yml(ctx, tbl, td, domain, out)
                written.append(p)
                print(f"  wrote {p.as_posix()}")
            except NotImplementedError as exc:
                print(f"  [skip] {exc}")

    # 4. classification.json
    p = emit.emit_classification_json(ctx, decisions, graph, domain, out)
    written.append(p)
    print(f"  wrote {p.as_posix()}")

    # 5. migration_review.md (stub header; full sections in Task 8)
    p = emit.emit_review_md(ctx, decisions, graph, domain, out)
    written.append(p)
    print(f"  wrote {p.as_posix()}")

    print(f"\n{len(written)} file(s) written to {out.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
