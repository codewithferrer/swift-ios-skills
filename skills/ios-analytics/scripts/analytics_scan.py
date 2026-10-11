#!/usr/bin/env python3
"""Read an iOS project's analytics: what it records, where it is blind, what is wrong.

    analytics_scan.py inventory   the events, their parameters and where they are sent from
    analytics_scan.py coverage    screens, controls and entry points that leave no record
    analytics_scan.py check       naming, limits, cardinality, privacy and configuration problems
    analytics_scan.py plan        the event plan against the code, and where each missing event would go

Everything reads only; nothing is written. Run it from the project folder, or
pass --root. Results come from reading the Swift sources by name, so they are
strong hints with a location each, not proofs.

Examples:
    python analytics_scan.py inventory
    python analytics_scan.py coverage --all
    python analytics_scan.py check --plan docs/PRODUCT.md --strict
    python analytics_scan.py plan --plan docs/PRODUCT.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import analytics_check as ck  # noqa: E402
import analytics_coverage as ac  # noqa: E402
import analytics_model as am  # noqa: E402
import analytics_plan as ap  # noqa: E402

LIMIT = 12


def rel(root: Path, path: Path, line: int | None = None) -> str:
    try:
        text = path.relative_to(root).as_posix()
    except ValueError:
        text = str(path)
    return f"{text}:{line}" if line else text


def load(args):
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"error: {root} is not a folder")
    model = am.scan(root, tuple(args.exclude or ()))
    if not model.files:
        raise SystemExit(f"error: no Swift files under {root}")
    return root, model


def table(rows: list, headers: list, indent: str = "  ") -> list:
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    out = [indent + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)).rstrip()]
    for r in rows:
        out.append(indent + "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())
    return out


# -- inventory ---------------------------------------------------------------------------------

def inventory_data(root: Path, model: am.Model) -> dict:
    events = model.events(include_debug=True)
    out_events = []
    for name, ev in sorted(events.items()):
        methods = [{"method": m.name, "owner": m.owner, "file": rel(root, m.file, m.line),
                    "calls": len(m.calls), "internal_calls": m.internal_calls,
                    "call_sites": [rel(root, c.file, c.line) for c in m.calls]} for m in ev["methods"]]
        debug = all(e.debug for m in ev["methods"] for e in m.emissions if e.event == name) and not ev["raw"] \
            if ev["methods"] else all(e.debug for e in ev["raw"])
        out_events.append({
            "event": name, "debug_only": debug, "parameters": {k: sorted(v) for k, v in sorted(ev["params"].items())},
            "methods": methods, "direct_sdk_calls": [rel(root, e.file, e.line) for e in ev["raw"]],
        })
    vocab = {}
    for method in model.methods():
        for em in method.emissions:
            for prm in em.params or []:
                if prm.kind == "string-param":
                    v = am.vocabulary(method, prm.expr.strip())
                    vocab[f"{em.event}.{prm.key}"] = {"values": v["literals"], "computed": sorted(set(v["dynamic"])),
                                                      "calls": v["calls"]}
    return {
        "root": str(root), "sdks": {k: [rel(root, p) for p in v][:3] for k, v in model.imports.items()},
        "firebase_products": sorted(model.firebase_products),
        "facades": {owner: {"file": rel(root, ms[0].file), "methods": len(ms)} for owner, ms in model.facades.items()},
        "config": [{"call": w, "where": rel(root, p, l), "detail": d} for w, p, l, d in model.config_calls],
        "events": out_events, "vocabulary": vocab,
        "never_called": [{"method": m.name, "where": rel(root, m.file, m.line)} for m in model.methods()
                         if not m.calls and not m.internal_calls],
        "dynamic_names": [{"expr": e.event_expr, "where": rel(root, e.file, e.line)} for e in model.emissions
                          if e.event is None],
    }


def cmd_inventory(args) -> int:
    root, model = load(args)
    data = inventory_data(root, model)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print(f"Analytics inventory: {root.name}")
    sdks = []
    for label in data["sdks"]:
        if label.startswith("Firebase Analytics") and data["firebase_products"]:
            sdks.append(f"{label} ({', '.join(data['firebase_products'])})")
        else:
            sdks.append(label)
    print("  SDKs: " + (", ".join(sdks) if sdks else "none found"))
    for owner, info in data["facades"].items():
        events = {e["event"] for e in data["events"]}
        print(f"  Facade: {owner} ({info['file']}), {info['methods']} methods")
    for c in data["config"]:
        if c["call"] == "setDefaultEventParameters":
            print(f"  Default parameters: {c['detail']}  ({c['where']})")
        elif c["call"] == "setAnalyticsCollectionEnabled":
            print(f"  Collection switch: setAnalyticsCollectionEnabled({c['detail']})  ({c['where']})")
        else:
            print(f"  {c['call']}  ({c['where']})")
    print()
    rows = []
    for e in data["events"]:
        params = ", ".join(e["parameters"]) or "-"
        senders = ", ".join(f"{m['method']} x{m['calls']}" for m in e["methods"]) or \
            ("direct SDK call x" + str(len(e["direct_sdk_calls"])))
        rows.append([e["event"] + (" [debug]" if e["debug_only"] else ""),
                     params if len(params) <= 52 else params[:49] + "...", senders])
    print(f"Events ({len(rows)})")
    print("\n".join(table(rows, ["event", "parameters", "sent from (calls)"])))
    if data["vocabulary"]:
        print("\nWhat callers pass for text parameters")
        for key, v in sorted(data["vocabulary"].items()):
            vals = ", ".join(f'"{k}"' + (f" x{n}" if n > 1 else "") for k, n in sorted(v["values"].items()))
            comp = ("; computed: " + ", ".join(v["computed"][:4])) if v["computed"] else ""
            if vals or comp:
                print(f"  {key}: {vals or '(no literal values)'}{comp}")
    if data["never_called"]:
        print("\nDefined but never called")
        for n in data["never_called"]:
            print(f"  {n['method']}  {n['where']}")
    if data["dynamic_names"]:
        print("\nEvent names built at runtime")
        for n in data["dynamic_names"]:
            print(f"  {n['expr']}  {n['where']}")
    return 0


# -- coverage ----------------------------------------------------------------------------------

def coverage_data(root: Path, model: am.Model, include_tests: bool) -> dict:
    cov = ac.analyse(model, include_tests)
    screens = [v for v in cov.views if v.presented]
    return {
        "views": {"total": len(cov.views), "tracked": sum(v.tracked for v in cov.views),
                  "screens": len(screens), "screens_tracked": sum(v.tracked for v in screens)},
        "untracked_screens": [{"view": v.name, "where": rel(root, v.file, v.line),
                               "presented": sorted({h for _, _, h in v.presented}), "controls": len(v.controls)}
                              for v in screens if not v.tracked],
        "controls": {"total": len(cov.controls),
                     "covered": sum(c.status == "covered" for c in cov.controls),
                     "uncovered": sum(c.status == "uncovered" for c in cov.controls),
                     "delegated": sum(c.status == "delegated" for c in cov.controls)},
        "gaps": [{"priority": c.priority, "category": c.category, "kind": c.kind, "label": c.label, "view": c.view,
                  "where": rel(root, c.file, c.line), "callees": c.callees[:6], "status": c.status,
                  "suggestion": ac.SUGGESTIONS.get(c.category, "")}
                 for c in cov.controls if c.status == "uncovered"],
        "delegated": [{"label": c.label, "view": c.view, "where": rel(root, c.file, c.line), "callees": c.callees[:4]}
                      for c in cov.controls if c.status == "delegated" and c.priority != "low"],
        "sites": [{"kind": s.kind, "priority": s.priority, "where": rel(root, s.file, s.line),
                   "in": f"{s.owner or ''}.{s.func or ''}".strip("."), "covered": s.covered, "note": s.note}
                  for s in cov.sites + cov.intents],
        "silent_catches": [{"priority": c.priority, "category": c.category, "where": rel(root, c.file, c.line),
                            "in": f"{c.owner or ''}.{c.func or ''}".strip("."), "note": c.note} for c in cov.catches],
        "tracked_screen_names": sorted(cov.tracked_screen_names),
    }


def cmd_coverage(args) -> int:
    root, model = load(args)
    data = coverage_data(root, model, args.include_tests)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    v, c = data["views"], data["controls"]
    print(f"Analytics coverage: {root.name}")
    if not model.methods() and not model.raw:
        print("  No events are recorded anywhere yet, so every control below is uncovered by definition. "
              "Use `plan` to see where the planned events would go.")
    print(f"  Screens: {v['screens']} presented ({v['screens_tracked']} tracked), {v['total']} views in total")
    print(f"  Controls: {c['total']} ({c['covered']} lead to an event, {c['uncovered']} do not, "
          f"{c['delegated']} hand off to a closure from the parent)")
    limit = None if args.all else LIMIT
    if data["untracked_screens"]:
        print("\nScreens with no tracking (presented by navigation, sheet or as the root)")
        for s in data["untracked_screens"][:limit]:
            print(f"  {s['view']}  {s['where']}  ({', '.join(s['presented'])}; {s['controls']} controls)")
    high = [g for g in data["gaps"] if g["priority"] == "high"]
    medium = [g for g in data["gaps"] if g["priority"] == "medium"]
    shown = high + (medium if args.all else [])
    if shown:
        print("\nControls with no event behind them, by how much a blind spot there costs")
        for g in shown[:limit]:
            label = f'"{g["label"]}"' if g["label"] else "(no label)"
            print(f"  [{g['priority']}] {g['kind']} {label} in {g['view']}  {g['where']}  ({g['category']})")
        if limit and len(shown) > limit:
            print(f"  ... and {len(shown) - limit} more (--all)")
        if not args.all and medium:
            print(f"  ({len(medium)} medium and {len(data['gaps']) - len(high) - len(medium)} low-priority "
                  "controls not shown; --all lists them)")
        cats = sorted({g["category"] for g in shown})
        print("\nWhat to record, by category")
        for cat in cats:
            if cat in ac.SUGGESTIONS:
                print(f"  {cat}: {ac.SUGGESTIONS[cat]}")
    if not shown and data["gaps"]:
        print(f"\nNo high-priority control is missing an event; {len(data['gaps'])} lower-priority controls have "
              "none (--all lists them)")
    gaps = [s for s in data["sites"] if not s["covered"]]
    if gaps:
        print("\nEntry points that leave no record")
        for s in gaps[:limit]:
            print(f"  [{s['priority']}] {s['kind']}  {s['where']}  in {s['in']}")
    done = [s for s in data["sites"] if s["covered"]]
    if done and args.all:
        print("\nEntry points that are recorded")
        for s in done:
            print(f"  {s['kind']}  {s['where']}  in {s['in']}  {s['note']}")
    if data["silent_catches"]:
        cs = data["silent_catches"]
        hi = [x for x in cs if x["priority"] == "high"]
        print(f"\nErrors that are caught and not recorded: {len(cs)} ({len(hi)} in purchase, permission or "
              "destructive code)")
        for x in (hi if not args.all else cs)[:limit]:
            print(f"  {x['where']}  in {x['in']}  ({x['note']})")
    return 0


# -- check -------------------------------------------------------------------------------------

def cmd_check(args) -> int:
    root, model = load(args)
    plan = am.parse_plan(Path(args.plan)) if args.plan else None
    issues = ck.check(model, plan)
    if args.json:
        print(json.dumps([{"severity": i.severity, "code": i.code, "message": i.message, "where": i.where}
                          for i in issues], indent=2, ensure_ascii=False))
    else:
        marks = {"error": "ERROR", "warning": "warn", "info": "info"}
        print(f"Analytics check: {root.name}")
        for i in issues:
            print(f"  {marks[i.severity]:<5} {i.code}: {i.message}")
            for w in i.where[: (None if args.all else 4)]:
                print(f"          {w}")
            if not args.all and len(i.where) > 4:
                print(f"          ... and {len(i.where) - 4} more (--all)")
        errors = sum(i.severity == "error" for i in issues)
        warnings = sum(i.severity == "warning" for i in issues)
        print(f"\n{errors} error(s), {warnings} warning(s), {len(issues) - errors - warnings} note(s)")
    errors = sum(i.severity == "error" for i in issues)
    warnings = sum(i.severity == "warning" for i in issues)
    return 1 if errors or (args.strict and warnings) else 0


# -- plan --------------------------------------------------------------------------------------

def cmd_plan(args) -> int:
    root, model = load(args)
    plan_path = Path(args.plan)
    if not plan_path.is_file():
        raise SystemExit(f"error: no such file: {plan_path}")
    plan = am.parse_plan(plan_path)
    if not plan:
        raise SystemExit(f"error: no event table found in {plan_path} (a markdown table whose first column is "
                         "'Event' and lists names in backticks)")
    events = model.events()
    cov = ac.analyse(model)
    rows = []
    for name, spec in plan.items():
        entry = {"event": name, "params": spec["params"], "planned_at": f"{plan_path.name}:{spec['line']}"}
        if name in events:
            ev = events[name]
            entry["status"] = "implemented"
            entry["sent_from"] = [m.name for m in ev["methods"]]
            entry["calls"] = sum(len(m.calls) for m in ev["methods"])
            entry["missing_params"] = [p for p in spec["params"] if p not in ev["params"]]
            entry["extra_params"] = sorted(k for k in ev["params"] if k not in spec["params"])
            if entry["calls"] == 0:
                entry["status"] = "defined, never called"
        else:
            entry["status"] = "not implemented"
            entry["candidates"] = [{"score": round(c.score, 1), "kind": c.kind, "where": rel(root, c.file, c.line),
                                    "signature": c.signature, "matched": c.matched}
                                   for c in ap.candidates_for(model, cov, name)]
        rows.append(entry)
    extra = sorted(n for n in events if n not in plan)
    data = {"plan": str(plan_path), "events": rows, "not_in_plan": extra}
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        done = sum(1 for r in rows if r["status"] == "implemented")
        print(f"Plan {plan_path.name}: {len(rows)} events, {done} implemented, "
              f"{sum(1 for r in rows if r['status'] == 'not implemented')} not in the code")
        for r in rows:
            mark = {"implemented": "ok  ", "defined, never called": "WARN", "not implemented": "TODO"}[r["status"]]
            params = f"({', '.join(r['params'])})" if r["params"] else ""
            print(f"\n  [{mark}] {r['event']} {params}")
            if r["status"] != "not implemented":
                print(f"         sent from {', '.join(r['sent_from']) or '-'}, {r['calls']} call(s)")
                if r["missing_params"]:
                    print(f"         missing parameters: {', '.join(r['missing_params'])}")
                if r["extra_params"]:
                    print(f"         not in the plan: {', '.join(r['extra_params'])}")
            else:
                if not r["candidates"]:
                    print("         no place in the code matches by name; the feature may not be built yet")
                for c in r["candidates"]:
                    print(f"         candidate: {c['kind']} {c['signature'][:70]}  {c['where']}")
        if extra:
            print(f"\n  Events in the code that the plan does not list: {', '.join(extra)}")
    missing = sum(1 for r in rows if r["status"] != "implemented")
    return 1 if args.strict and (missing or extra) else 0


# -- entry point -------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="Project folder (default: the current folder)")
    common.add_argument("--exclude", action="append", metavar="TEXT",
                        help="Skip Swift files whose path contains TEXT (repeatable, e.g. 'Watch App')")
    common.add_argument("--json", action="store_true", help="Machine-readable output")
    common.add_argument("--all", action="store_true", help="Do not shorten lists")

    inv = sub.add_parser("inventory", parents=[common], help="Events, parameters and who sends them")
    inv.set_defaults(handler=cmd_inventory)
    cov = sub.add_parser("coverage", parents=[common], help="Screens, controls and entry points with no record")
    cov.add_argument("--include-tests", action="store_true", help="Also read test and preview code")
    cov.set_defaults(handler=cmd_coverage)
    chk = sub.add_parser("check", parents=[common], help="Naming, limits, cardinality, privacy, configuration")
    chk.add_argument("--plan", help="Markdown file with an event table, to compare against")
    chk.add_argument("--strict", action="store_true", help="Exit 1 on warnings too")
    chk.set_defaults(handler=cmd_check)
    pln = sub.add_parser("plan", parents=[common], help="The plan against the code, and where missing events go")
    pln.add_argument("--plan", required=True, help="Markdown file with an event table")
    pln.add_argument("--strict", action="store_true", help="Exit 1 if the plan and the code differ")
    pln.set_defaults(handler=cmd_plan)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
