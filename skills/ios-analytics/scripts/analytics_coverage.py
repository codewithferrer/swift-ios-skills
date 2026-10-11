"""Where the app is observable and where it is not.

Starting from the typed analytics calls found by analytics_model, this walks
the SwiftUI code: which views are tracked, which controls lead (directly or
through the functions they call) to an analytics call, which `catch` blocks
swallow an error without a trace, and which entry points (permission prompts,
purchases, App Intents) leave no record. Resolution is by function name, so a
result is a strong hint, not a proof; every item carries its location so it can
be checked in seconds.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import Path

import analytics_model as am
import swift_source as sw

CONTROL_RE = re.compile(
    r"(?<![\w.])(Button|Toggle|Picker|Stepper|Slider|DatePicker|ColorPicker|Link|ShareLink|PhotosPicker|"
    r"NavigationLink)\s*(?=[({])")
GESTURE_RE = re.compile(r"\.(onTapGesture|onLongPressGesture|onSubmit|refreshable)\s*(?=[({])")
PRESENT_RE = re.compile(r"\.(sheet|fullScreenCover|popover|navigationDestination)\s*\(|(?<![\w.])NavigationLink\s*\(")
BINDING_ARG = {"Toggle": "isOn", "Picker": "selection", "Stepper": "value", "Slider": "value",
               "DatePicker": "selection", "ColorPicker": "selection", "PhotosPicker": "selection"}

CATEGORIES = (
    ("monetization", "high", re.compile(r"(?i)purchas|\bbuy\b|subscri|restore|paywall|unlock|upgrade|premium|\bpro\b|"
                                         r"price|offer|redeem|donat|\btip\b")),
    ("permission", "high", re.compile(r"(?i)permission|authoriz|\ballow|notification|camera|location|motion|"
                                      r"microphone|health|tracking|reminder")),
    ("destructive", "high", re.compile(r"(?i)delet|remov|reset|clear|erase|wipe|log ?out|sign ?out|discard")),
    ("core loop", "high", re.compile(r"(?i)\bstart|\bplay|\bbegin|new ?game|finish|complete|submit|solve|answer|retry|"
                                     r"restart|resume|pause|quit|give ?up|surrender|\bskip|hint|undo|next ?level")),
    ("sharing and feedback", "medium", re.compile(r"(?i)share|export|invite|\brate\b|review|feedback|contact|support|"
                                                  r"\bbug\b|report")),
    ("preferences", "medium", re.compile(r"(?i)difficult|\bmode\b|theme|palette|sound|music|haptic|vibrat|language|"
                                         r"speed|\bsize\b|layout|style|clock|timer|alarm|challenge|volume")),
    ("navigation", "low", re.compile(r"(?i)\bclose|dismiss|\bback\b|cancel|\bdone\b|\bok\b|\bnext\b|got ?it|later")),
)
SYSTEM_SITES = (
    ("permission prompt", "high", {"requestAuthorization", "requestAccess", "requestPermission",
                                   "requestWhenInUseAuthorization", "requestAlwaysAuthorization",
                                   "requestPermissions", "requestRecordPermission"}),
    ("purchase flow", "high", {"purchase", "restorePurchases", "showManageSubscriptions",
                               "presentCodeRedemptionSheet", "beginRefundRequest"}),
    ("rating prompt", "medium", {"requestReview"}),
    ("share sheet", "medium", {"UIActivityViewController"}),
)
SUGGESTIONS = {
    "monetization": "record each outcome of the flow (shown, started, succeeded, cancelled, failed, restored) so the funnel has no gap",
    "permission": "record the prompt and its result (granted or denied): it is the first place users drop",
    "destructive": "record that it happened and what it removed (a count, not the content)",
    "core loop": "record start and end of the loop with the settings in force, so every session can be segmented",
    "sharing and feedback": "record that it was used and where from; low volume, high intent",
    "preferences": "either record the change, or make sure the value rides on the start/end events of the core loop",
    "navigation": "usually not worth an event; screen tracking already shows the path",
}


def classify(*texts: str):
    """Category and priority from the first text that says something: label, then callees, then the rest."""
    for text in texts:
        if not text:
            continue
        for name, priority, rx in CATEGORIES:
            if rx.search(text):
                return name, priority
    return "other", "low"


@dataclass
class Control:
    kind: str
    label: str | None
    file: Path
    line: int
    view: str | None
    status: str                     # covered | uncovered | delegated
    via: list = field(default_factory=list)
    category: str = "other"
    priority: str = "low"
    callees: list = field(default_factory=list)


@dataclass
class ViewInfo:
    name: str
    file: Path
    line: int
    tracked: bool
    evidence: str
    presented: list = field(default_factory=list)     # [(file, line, how)]
    controls: list = field(default_factory=list)


@dataclass
class SilentCatch:
    file: Path
    line: int
    func: str | None
    owner: str | None
    note: str                       # "no record" | "logged locally only"
    category: str
    priority: str


@dataclass
class Site:
    kind: str
    priority: str
    file: Path
    line: int
    func: str | None
    owner: str | None
    covered: bool
    note: str = ""


@dataclass
class Coverage:
    views: list
    controls: list
    catches: list
    sites: list
    intents: list
    tracked_screen_names: set


class Index:
    """Function index and analytics offsets, for reachability."""

    def __init__(self, model: am.Model):
        self.model = model
        self.by_name: dict = {}
        self.sf_by_path = {sf.path: sf for sf in model.files}
        for sf in model.files:
            for fn in sf.funcs:
                if fn.body and fn.body[1] is not None:
                    self.by_name.setdefault(fn.name, []).append((sf, fn))
        self.offsets: dict = {}
        for method in model.methods():
            for site in method.calls:
                self.offsets.setdefault(site.file, []).append(site.offset)
            for em in method.emissions:
                self.offsets.setdefault(em.file, []).append(em.offset)
        for em in model.raw:
            self.offsets.setdefault(em.file, []).append(em.offset)
        # a call to a facade method from inside the facade itself still is analytics
        for path in self.offsets:
            self.offsets[path].sort()
        self.facade_methods = {m.name for m in model.methods()}
        self._memo: dict = {}

    def direct(self, sf: sw.SwiftFile, a: int, b: int) -> bool:
        pts = self.offsets.get(sf.path)
        if not pts:
            return False
        i = bisect.bisect_left(pts, a)
        return i < len(pts) and pts[i] < b

    def reach(self, sf: sw.SwiftFile, a: int, b: int, depth: int = 3, seen: set | None = None):
        """(True, [names]) if the range reaches analytics, directly or through project functions."""
        if self.direct(sf, a, b):
            return True, []
        if depth == 0:
            return False, []
        seen = seen if seen is not None else set()
        for call in sw.calls_in(sf, a, b):
            for sf2, fn in self.by_name.get(call.name, [])[:4]:
                key = (sf2.path, fn.start)
                if key in seen:
                    continue
                seen.add(key)
                hit, path = self.reach(sf2, fn.body[0], fn.body[1], depth - 1, seen)
                if hit:
                    return True, [call.name] + path
        return False, []

    def callee_names(self, sf: sw.SwiftFile, a: int, b: int) -> list:
        return sorted({c.name for c in sw.calls_in(sf, a, b)})

    def callers_have_analytics(self, names: set, own_path: Path, own_start: int) -> bool:
        """Does any function that calls one of `names` record analytics in its own body?"""
        if not names:
            return False
        if not hasattr(self, "_callers"):
            self._callers: dict = {}
            for sf in self.model.files:
                for fn in sf.funcs:
                    if not (fn.body and fn.body[1] is not None):
                        continue
                    for call in sw.calls_in(sf, fn.body[0], fn.body[1]):
                        self._callers.setdefault(call.name, []).append((sf, fn))
        for name in names:
            for sf, fn in self._callers.get(name, []):
                if sf.path == own_path and fn.start == own_start:
                    continue
                if self.direct(sf, fn.body[0], fn.body[1]):
                    return True
        return False


_CLOSURE_PROP = re.compile(r"\b(?:var|let)\s+([A-Za-z_]\w*)\s*:\s*(?:@escaping\s+|@MainActor\s+|@Sendable\s+)*\(")


def _label_text(sf: sw.SwiftFile, a: int, b: int) -> str | None:
    for q in sorted(k for k in sf.strings if a <= k < b):
        lit = sf.strings[q]
        if lit.value.strip():
            return lit.value
    chunk = sf.text[a:b].strip()
    m = re.search(r"\b(?:Text|Label)\s*\(\s*([A-Za-z_][\w.]*)", chunk)
    return m.group(1) if m else None


def _parse_control(sf: sw.SwiftFile, m: re.Match):
    """(label, action_ranges, binding_expr, end_offset) for the control at match m."""
    kind = m.group(1)
    pos = m.end()
    label = None
    action_arg = None
    binding = None
    end = pos
    if sf.code[pos] == "(":
        close = sf.pairs.get(pos)
        if close is None:
            return None
        end = close + 1
        for lab, a, b in sw.split_args(sf, pos):
            if lab is None and label is None:
                lit = sw.first_string(sf, a, b)
                if lit is not None:
                    label = lit.value
            elif lab == "action":
                action_arg = (a, b)
            elif lab == BINDING_ARG.get(kind):
                binding = sf.text[a:b].strip()
    ranges = []
    trailing = sw.trailing_closure(sf, end - 1, len(sf.code)) if sf.code[pos] == "(" else (
        (pos, sf.pairs.get(pos)) if sf.code[pos] == "{" and sf.pairs.get(pos) else None)
    if kind == "Button":
        if action_arg is not None:
            ranges.append(action_arg)
            if trailing and label is None:
                label = _label_text(sf, trailing[0], trailing[1])
        elif trailing:
            ranges.append((trailing[0], trailing[1]))
            tail = sf.code[trailing[1] + 1:trailing[1] + 60]
            lab = re.match(r"\s*label\s*:\s*\{", tail)
            if lab and label is None:
                o = trailing[1] + 1 + lab.end() - 1
                c = sf.pairs.get(o)
                if c:
                    label = _label_text(sf, o, c)
    else:
        if trailing and label is None:
            label = _label_text(sf, trailing[0], trailing[1])
        if action_arg is not None:
            ranges.append(action_arg)
        if binding is None and kind in BINDING_ARG and trailing is None and sf.code[pos] == "(":
            pass
    return kind, label, ranges, binding, end


def _view_types(sf: sw.SwiftFile):
    for decl in sf.types:
        if decl.body and decl.body[1] is not None and re.search(r"\bView\b", decl.inherits) \
                and decl.keyword in ("struct", "class"):
            yield decl


def _binding_property(expr: str | None) -> str | None:
    if not expr:
        return None
    ids = re.findall(r"[A-Za-z_]\w*", expr.replace("wrappedValue", ""))
    ids = [i for i in ids if i not in ("Binding", "get", "set", "newValue", "self")]
    return ids[-1] if ids else None


def analyse(model: am.Model, include_tests: bool = False) -> Coverage:
    idx = Index(model)
    screen_names: set = set()
    for method in model.methods():
        if any(e.event == "screen_view" for e in method.emissions):
            for site in method.calls:
                for lab, expr, lit in site.args:
                    if lit:
                        screen_names.add(lit)
            for em in method.emissions:
                pass
    views: dict = {}
    controls: list = []

    def skip(path: Path) -> bool:
        parts = {p.lower() for p in path.parts}
        return (not include_tests) and any(
            part.endswith("tests") or part.endswith("uitests") or part == "previews" for part in parts)

    # views and presentation anchors
    all_view_names = set()
    for sf in model.files:
        if skip(sf.path):
            continue
        for decl in _view_types(sf):
            all_view_names.add(decl.name)
    for sf in model.files:
        if skip(sf.path):
            continue
        for decl in _view_types(sf):
            a, b = decl.body
            tracked = idx.direct(sf, a, b)
            norm = re.sub(r"(?:View|Screen|Sheet|Page)$", "", decl.name)
            snake = am.snake(norm) if norm else am.snake(decl.name)
            evidence = "analytics call in the view" if tracked else ""
            if not tracked and snake in screen_names:
                tracked, evidence = True, f"screen_view \"{snake}\" is logged on navigation"
            views[decl.name] = ViewInfo(decl.name, sf.path, sf.line(decl.start), tracked, evidence)
    for sf in model.files:
        if skip(sf.path):
            continue
        for m in PRESENT_RE.finditer(sf.code):
            open_p = m.end() - 1
            close = sf.pairs.get(open_p)
            if close is None:
                continue
            end = close
            tr = sw.trailing_closure(sf, close, len(sf.code))
            if tr:
                end = tr[1]
            how = m.group(1) or "NavigationLink"
            for nm in re.finditer(r"(?<![\w.])([A-Z]\w*)\s*\(", sf.code[open_p:end + 1]):
                if nm.group(1) in views:
                    views[nm.group(1)].presented.append((sf.path, sf.line(open_p), how))

    for sf in model.files:
        if skip(sf.path):
            continue
        for m in re.finditer(r"\bWindowGroup\b[^{]*\{", sf.code):
            close = sf.pairs.get(m.end() - 1)
            if close:
                nm = re.search(r"(?<![\w.])([A-Z]\w*)\s*\(", sf.code[m.end():close])
                if nm and nm.group(1) in views:
                    views[nm.group(1)].presented.append((sf.path, sf.line(m.start()), "WindowGroup"))

    # controls
    for sf in model.files:
        if skip(sf.path):
            continue
        for decl in _view_types(sf):
            a, b = decl.body
            closure_props = set(_CLOSURE_PROP.findall(sf.code[a:b]))
            matches = [(m, "control") for m in CONTROL_RE.finditer(sf.code, a, b)]
            matches += [(m, "gesture") for m in GESTURE_RE.finditer(sf.code, a, b)]
            for m, flavour in matches:
                if flavour == "gesture":
                    kind = m.group(1)
                    pos = m.end()
                    ranges, label, binding = [], None, None
                    if sf.code[pos] == "{" and sf.pairs.get(pos):
                        ranges.append((pos, sf.pairs[pos]))
                    elif sf.code[pos] == "(":
                        c = sf.pairs.get(pos)
                        tr = sw.trailing_closure(sf, c, len(sf.code)) if c else None
                        if tr:
                            ranges.append(tr)
                        for lab, x, y in sw.split_args(sf, pos):
                            if lab in ("perform", "action"):
                                ranges.append((x, y))
                else:
                    parsed = _parse_control(sf, m)
                    if parsed is None:
                        continue
                    kind, label, ranges, binding, _ = parsed
                    if kind == "NavigationLink":
                        continue          # navigation is covered by screen tracking
                status, via, callees = "uncovered", [], []
                for ra, rb in ranges:
                    hit, path = idx.reach(sf, ra, rb)
                    callees += idx.callee_names(sf, ra, rb)
                    ident = re.fullmatch(r"\s*(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\s*", sf.code[ra:rb])
                    if ident:
                        callees.append(ident.group(1))
                        for sf2, fn in idx.by_name.get(ident.group(1), [])[:4]:
                            h2, p2 = idx.reach(sf2, fn.body[0], fn.body[1], depth=2)
                            if h2:
                                hit, path = True, [ident.group(1)] + p2
                                break
                    if hit:
                        status, via = "covered", path
                        break
                if status != "covered" and binding is not None or (kind in BINDING_ARG and binding):
                    prop = _binding_property(binding)
                    if prop:
                        for oc in re.finditer(r"\.onChange\s*\(\s*of\s*:", sf.code[a:b]):
                            op = a + oc.end() - 1
                            # the call's closure
                            call_open = sf.code.rfind("(", a, oc.end())
                            cl = sf.pairs.get(call_open)
                            if cl is None:
                                continue
                            if prop in sf.code[call_open:cl]:
                                tr = sw.trailing_closure(sf, cl, len(sf.code))
                                rng = tr or (call_open, cl)
                                hit, path = idx.reach(sf, rng[0], rng[1])
                                if hit:
                                    status, via = "covered", [f"onChange({prop})"] + path
                                    break
                        if status != "covered":
                            for name_, cands in idx.by_name.items():
                                if name_ in (f"{prop}.didSet", f"{prop}.willSet"):
                                    for sf2, fn in cands:
                                        hit, path = idx.reach(sf2, fn.body[0], fn.body[1])
                                        if hit:
                                            status, via = "covered", [name_] + path
                                            break
                        if status != "covered" and binding and "Binding(" in binding:
                            status = "uncovered"
                        callees.append(prop)
                if status != "covered" and any(c in closure_props or c == "dismiss" for c in callees) \
                        and not any(c in idx.by_name for c in callees if c not in closure_props and c != "dismiss"):
                    status = "delegated"
                view_name = decl.name
                cat, prio = classify(label or "", " ".join(callees), binding or "")
                controls.append(Control(kind, label, sf.path, sf.line(m.start()), view_name, status, via, cat, prio,
                                        callees))
                if view_name in views:
                    views[view_name].controls.append(controls[-1])

    # catch blocks
    catches: list = []
    for sf in model.files:
        if skip(sf.path):
            continue
        for m in re.finditer(r"\bcatch\b", sf.code):
            j = m.end()
            while j < len(sf.code) and sf.code[j] != "{" and sf.code[j] != "\n":
                j += 1
            if j >= len(sf.code) or sf.code[j] != "{":
                continue
            close = sf.pairs.get(j)
            if close is None:
                continue
            hit, _ = idx.reach(sf, j, close, depth=2)
            body = sf.code[j:close]
            if hit or re.search(r"\bthrow\b|Crashlytics|\.record\(|recordError|fatalError", body):
                continue
            fn = sf.func_at(m.start())
            owner = sf.type_at(m.start())
            local = bool(re.search(r"\bprint\s*\(|Logger|os_log|NSLog|logger\.|\.error\(|\.log\(", body))
            cat, prio = classify(fn.name if fn else "", owner.name if owner else "", sf.path.stem)
            catches.append(SilentCatch(sf.path, sf.line(m.start()), fn.name if fn else None,
                                       owner.name if owner else None,
                                       "logged locally only" if local else "no record", cat, prio))

    # system sites
    sites: list = []
    for sf in model.files:
        if skip(sf.path):
            continue
        for call in sw.calls_in(sf, 0, len(sf.code)):
            for kind, prio, names in SYSTEM_SITES:
                if call.name in names:
                    fn = sf.func_at(call.start)
                    owner = sf.type_at(call.start)
                    if owner and owner.name in model.facades:
                        continue
                    hit = False
                    note = ""
                    if fn is not None and fn.body:
                        hit, _ = idx.reach(sf, fn.body[0], fn.body[1], depth=2)
                        if not hit:
                            names = {fn.name} if fn.name not in ("body", "init") else set()
                            if owner is not None and fn.name in ("makeUIViewController", "makeUIView", "body", "init"):
                                names.add(owner.name)
                            hit = idx.callers_have_analytics(names, sf.path, fn.start)
                            note = "recorded by the caller" if hit else ""
                    sites.append(Site(kind, prio, sf.path, sf.line(call.start), fn.name if fn else None,
                                      owner.name if owner else None, hit, note))

    # App Intents
    intents: list = []
    for sf in model.files:
        if skip(sf.path):
            continue
        for decl in sf.types:
            if decl.body and decl.body[1] is not None and re.search(r"\b\w*Intent\b", decl.inherits) \
                    and decl.keyword in ("struct", "class", "enum"):
                perform = next((f for f in sf.funcs if f.name == "perform" and f.body
                                and decl.body[0] < f.start < decl.body[1]), None)
                hit = False
                if perform:
                    hit, _ = idx.reach(sf, perform.body[0], perform.body[1], depth=3)
                intents.append(Site("App Intent", "medium", sf.path, sf.line(decl.start), "perform", decl.name, hit))

    return Coverage(sorted(views.values(), key=lambda v: (v.file.as_posix(), v.line)), controls, catches, sites,
                    intents, screen_names)
