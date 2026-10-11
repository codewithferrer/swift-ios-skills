#!/usr/bin/env python3
"""Check, translate and update Xcode String Catalogs (.xcstrings).

    catalog.py audit [CATALOG ...]            coverage per language and what is wrong
    catalog.py todo  --catalog C --locale de  the keys that need a translation, as JSON
    catalog.py apply --catalog C --file F     put translations into the catalog

`audit` and `todo` only read. `apply` shows what it would change and writes
only with --write. The catalog is re-emitted in the exact format it already
has, so a change to three keys is a diff of three keys; if the file cannot be
reproduced byte for byte the tool refuses to write it.

Examples:
    python catalog.py audit
    python catalog.py audit App/Localizable.xcstrings --locales de,ja --json
    python catalog.py todo --catalog App/Localizable.xcstrings --locale de --examples 15 --out de.todo.json
    python catalog.py apply --catalog App/Localizable.xcstrings --file de.json
    python catalog.py apply --catalog App/Localizable.xcstrings --file de.json --write
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import catalog_logic as cl  # noqa: E402
import xcstrings as xc  # noqa: E402

SAMPLE = 5


def shown(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def split(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


class SourceCache:
    """Swift sources are read once per project, however many catalogs it has."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._by_root: dict[Path, str] = {}

    def for_catalog(self, path: Path) -> str | None:
        if not self.enabled:
            return None
        root = cl.project_root(path)
        if root not in self._by_root:
            self._by_root[root] = cl.read_sources(root)
        return self._by_root[root]


def resolve_catalogs(paths: list[str] | None, root: str | None) -> list[Path]:
    if paths:
        missing = [p for p in paths if not Path(p).is_file()]
        if missing:
            raise xc.CatalogError(f"no such file: {', '.join(missing)}")
        return [Path(p) for p in paths]
    base = Path(root) if root else Path.cwd()
    found = cl.find_catalogs(base)
    if not found:
        raise xc.CatalogError(f"no .xcstrings file under {base}; pass the path to a catalog")
    return found


def single_catalog(path: str | None, root: str | None) -> Path:
    if path:
        return resolve_catalogs([path], None)[0]
    found = resolve_catalogs(None, root)
    if len(found) > 1:
        raise xc.CatalogError("several catalogs found, pick one with --catalog:\n  "
                              + "\n  ".join(shown(p) for p in found))
    return found[0]


# -- audit -------------------------------------------------------------------------------------

def grouped(findings: list[cl.Finding]) -> list[tuple[cl.Finding, list[str]]]:
    """One row per identical finding, with the languages it applies to."""
    rows: dict[tuple, tuple[cl.Finding, list[str]]] = {}
    for f in findings:
        key = (f.severity, f.code, f.message, tuple(f.details))
        if key not in rows:
            rows[key] = (f, [])
        if f.locale:
            rows[key][1].append(f.locale)
    return list(rows.values())


def print_audit(result: cl.AuditResult, show_all: bool) -> None:
    print(shown(result.path))
    counted = f"{result.translatable} to translate"
    notes = []
    if result.skipped["no_text"]:
        notes.append(f"{result.skipped['no_text']} without text")
    if result.skipped["should_not_translate"]:
        notes.append(f"{result.skipped['should_not_translate']} marked Don't Translate")
    if result.skipped["stale_unreferenced"]:
        notes.append(f"{result.skipped['stale_unreferenced']} stale and unused")
    print(f"  source {result.source_language} · version {result.version} · {result.key_count} keys, {counted}"
          + (f" (not counted: {', '.join(notes)})" if notes else ""))
    print("  this tool can write the file" if result.writable
          else f"  this tool will NOT write the file: {result.writable_note}")
    if not result.scanned_sources:
        print("  Swift sources not scanned: stale keys are counted as in use")
    if result.coverage:
        print()
        print(f"  {'language':<10} {'translated':>10} {'review':>7} {'new':>5} {'missing':>8} {'done':>7}")
        for locale, counts in result.coverage.items():
            total = sum(counts.values()) or 1
            print(f"  {locale:<10} {counts['translated']:>10} {counts['needs_review']:>7} {counts['new']:>5} "
                  f"{counts['missing']:>8} {100 * counts['translated'] / total:>6.1f}%")
    marks = {"error": "ERROR", "warning": "warn", "info": "info"}
    rows = grouped(result.findings)
    if rows:
        print()
    for finding, locales in rows:
        where = f" [{', '.join(locales)}]" if locales else ""
        print(f"  {marks[finding.severity]:<5} {finding.code}{where}: {finding.message}")
        limit = None if show_all else SAMPLE
        for line in finding.details[:limit]:
            print(f"          {line}")
        if limit and len(finding.details) > limit:
            print(f"          ... and {len(finding.details) - limit} more (--all to list them)")
    print()


def cmd_audit(args) -> int:
    paths = resolve_catalogs(args.catalog, args.root)
    cache = SourceCache(not args.no_source_scan)
    results = []
    for path in paths:
        catalog = xc.load(path)
        results.append(cl.audit(catalog, split(args.locales), cache.for_catalog(path), not args.no_compile))
    if args.json:
        print(json.dumps([cl.audit_to_dict(r) for r in results], indent=2, ensure_ascii=False))
    else:
        for result in results:
            print_audit(result, args.all)
        errors = sum(r.errors for r in results)
        warnings = sum(r.warnings for r in results)
        print(f"{len(results)} catalog(s): {errors} error(s), {warnings} warning(s)")
    failing = sum(r.errors for r in results) + (sum(r.warnings for r in results) if args.strict else 0)
    return 1 if failing else 0


# -- todo --------------------------------------------------------------------------------------

def cmd_todo(args) -> int:
    path = single_catalog(args.catalog, args.root)
    catalog = xc.load(path)
    include = split(args.include) or [cl.MISSING, cl.NEW, cl.NEEDS_REVIEW]
    unknown = [i for i in include if i not in cl.STATUSES]
    if unknown:
        raise xc.CatalogError(f"--include takes {', '.join(cl.STATUSES)}, not {', '.join(unknown)}")
    sources = SourceCache(not args.no_source_scan).for_catalog(path)
    work = cl.todo(catalog, args.locale, include, args.examples, sources, args.limit, split(args.reference))
    text = json.dumps(work, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    counts: dict[str, int] = {}
    for item in work["items"]:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    detail = ", ".join(f"{n} {r}" for r, n in sorted(counts.items())) or "nothing"
    where = f" -> {args.out}" if args.out else ""
    print(f"{work['total']} key(s) to translate into {args.locale} ({detail}){where}", file=sys.stderr)
    if "not_handled" in work:
        print(f"{len(work['not_handled']['keys'])} key(s) use device variations or substitutions and are not "
              "included: edit them in Xcode", file=sys.stderr)
    return 0


# -- apply -------------------------------------------------------------------------------------

def describe(value) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {v!r}" for k, v in value.items()) + "}"
    return repr(value)


def cmd_apply(args) -> int:
    path = single_catalog(args.catalog, args.root)
    catalog = xc.load(path)
    try:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise xc.CatalogError(f"cannot read {args.file}: {exc}") from exc
    if not isinstance(payload, dict):
        raise xc.CatalogError(f"{args.file} must hold a JSON object with 'language' and 'translations'")
    sources = SourceCache(not args.no_source_scan).for_catalog(path)
    plan = cl.plan_apply(catalog, payload, args.state, args.overwrite, args.language, sources)

    print(f"Catalog: {shown(path)}")
    print(f"Language: {plan.language}   state written: {plan.state}")
    if plan.problems:
        print(f"\nCannot apply -- {len(plan.problems)} problem(s), nothing was written:", file=sys.stderr)
        for problem in plan.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    for change in plan.changes:
        label = {"missing": "new", "new": "new", "needs_review": "review", "translated": "replace"}[change.status_before]
        print(f"  {label:<8} {change.key}")
        if change.before is not None:
            print(f"           was  {describe(change.before)}")
        print(f"           now  {describe(change.after)}")
    print(f"\n{len(plan.changes)} change(s), {len(plan.unchanged)} already as given.")
    if not plan.changes:
        return 0
    if not args.write:
        print("Preview only. Pass --write to change the file.")
        return 0

    data = cl.apply_plan(catalog, plan)
    untouched = [k for k in catalog.data["strings"] if k not in {c.key for c in plan.changes}]
    if any(data["strings"][k] != catalog.data["strings"][k] for k in untouched) \
            or list(data["strings"]) != list(catalog.data["strings"]):
        raise xc.CatalogError("internal check failed: a key outside the plan would change; nothing was written")
    new_text = xc.save(catalog, data)
    changed = [l for l in difflib.unified_diff(catalog.text.splitlines(), new_text.splitlines(), lineterm="", n=0)
               if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    added = sum(1 for l in changed if l[0] == "+")
    print(f"Written: {added} line(s) added, {len(changed) - added} removed in {path.name}.")
    after = cl.audit(xc.load(path), [plan.language], sources)
    counts = after.coverage.get(plan.language, {})
    print(f"{plan.language} now: {counts.get('translated', 0)} translated, {counts.get('needs_review', 0)} need review, "
          f"{counts.get('new', 0)} new, {counts.get('missing', 0)} missing; {after.errors} error(s).")
    return 0 if after.errors == 0 else 1


# -- entry point -------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="Folder to look for catalogs in (default: the current folder)")
    common.add_argument("--no-source-scan", action="store_true",
                        help="Do not read the Swift sources (stale keys are then counted as in use)")

    audit = sub.add_parser("audit", parents=[common], help="Coverage and problems; reads only")
    audit.add_argument("catalog", nargs="*", help="Catalog files (default: every .xcstrings under --root)")
    audit.add_argument("--locales", help="Only these languages, comma separated (default: all in the catalog)")
    audit.add_argument("--no-compile", action="store_true",
                       help="Do not also run Xcode's xcstringstool on the file (it is skipped where Xcode is absent)")
    audit.add_argument("--json", action="store_true", help="Machine-readable output")
    audit.add_argument("--all", action="store_true", help=f"List every key, not the first {SAMPLE}")
    audit.add_argument("--strict", action="store_true", help="Exit 1 on warnings too")
    audit.set_defaults(handler=cmd_audit)

    todo = sub.add_parser("todo", parents=[common], help="The work list for one language, as JSON; reads only")
    todo.add_argument("--catalog", help="The catalog (default: the only one under --root)")
    todo.add_argument("--locale", required=True, help="Target language, e.g. de, ja, zh-Hans, es-419")
    todo.add_argument("--include", help="Which to list: missing,new,needs_review (default: all three)")
    todo.add_argument("--examples", type=int, default=0, metavar="N",
                      help="Add N already translated pairs for terminology and tone")
    todo.add_argument("--reference", metavar="LOCALES",
                      help="Add each key's existing translation in these languages (e.g. es), so the "
                           "author's choices of meaning carry over")
    todo.add_argument("--limit", type=int, help="Stop the list after N keys (the total is still reported)")
    todo.add_argument("--out", help="Write the JSON here instead of stdout")
    todo.set_defaults(handler=cmd_todo)

    apply = sub.add_parser("apply", parents=[common], help="Put translations into the catalog (preview unless --write)")
    apply.add_argument("--catalog", help="The catalog (default: the only one under --root)")
    apply.add_argument("--file", required=True, help='JSON: {"language": "de", "translations": {"key": "text"}}')
    apply.add_argument("--language", help="Overrides 'language' in the file")
    apply.add_argument("--state", default=cl.NEEDS_REVIEW, choices=[cl.NEEDS_REVIEW, cl.TRANSLATED],
                       help="State to record (default needs_review: machine translations await a human)")
    apply.add_argument("--overwrite", action="store_true", help="Replace values that are already translated")
    apply.add_argument("--write", action="store_true", help="Write the file (default: preview)")
    apply.set_defaults(handler=cmd_apply)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except xc.CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
