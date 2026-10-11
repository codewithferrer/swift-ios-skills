"""What a project's analytics layer looks like, read from its Swift sources.

The model is built in layers because that is how apps are written: an SDK call
(`Analytics.logEvent`), often behind a private wrapper (`log(_:_:)`), behind a
typed facade (`AnalyticsManager.shared.gameStart(...)`), called from views and
view models. The scan recovers each layer, so an event can be traced from the
name that reaches the SDK to the screen that triggers it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import swift_source as sw


# -- SDK knowledge -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Sink:
    sdk: str
    name: str                    # the function that records an event
    receivers: tuple | None      # accepted receivers; None = any
    name_label: str | None       # label of the event-name argument (None = first, unlabelled)
    params_label: str | None     # label of the parameters argument


SINKS = (
    Sink("Firebase Analytics", "logEvent", ("Analytics",), None, "parameters"),
    Sink("TelemetryDeck", "signal", ("TelemetryDeck",), None, "parameters"),
    Sink("PostHog", "capture", ("PostHogSDK.shared", "PostHogSDK", "PostHog"), None, "properties"),
    Sink("Mixpanel", "track", None, "event", "properties"),
    Sink("Amplitude", "logEvent", None, None, "withEventProperties"),
    Sink("Aptabase", "trackEvent", ("Aptabase.shared", "Aptabase"), None, "with"),
)
HANDLER_NAME = re.compile(r"^(?:log|track|send|emit|record|analytics)\w*(?:Handler|Callback|Sink)$")
IMPORT_SDKS = {
    "FirebaseAnalytics": "Firebase Analytics", "FirebaseAnalyticsCore": "Firebase Analytics (no IDFA)",
    "FirebaseAnalyticsIdentitySupport": "Firebase Analytics identity support",
    "FirebaseCrashlytics": "Firebase Crashlytics", "TelemetryDeck": "TelemetryDeck", "PostHog": "PostHog",
    "Mixpanel": "Mixpanel", "Amplitude": "Amplitude", "AmplitudeSwift": "Amplitude", "Aptabase": "Aptabase",
    "AppTrackingTransparency": "AppTrackingTransparency", "AdSupport": "AdSupport (IDFA)",
    "RevenueCat": "RevenueCat", "Purchases": "RevenueCat",
}
FIREBASE_CONSTANT = re.compile(r"^AnalyticsEvent([A-Z]\w*)$")
FIREBASE_PARAM_CONSTANT = re.compile(r"^AnalyticsParameter([A-Z]\w*)$")


def snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


# -- records -------------------------------------------------------------------------------------

@dataclass
class Param:
    key: str
    expr: str
    kind: str


@dataclass
class Emission:
    event: str | None
    event_expr: str
    params: list | None            # None when the parameters are not a dictionary literal
    sdk: str
    file: Path
    line: int
    owner: str | None
    func: str | None
    func_params: str = ""
    via: str = ""                  # wrapper name when the call went through one
    offset: int = 0
    debug: bool = False            # inside #if DEBUG


@dataclass
class CallSite:
    file: Path
    line: int
    owner: str | None
    func: str | None
    receiver: str
    resolved: str                  # "exact" | "by name"
    offset: int = 0
    args: list = field(default_factory=list)   # [(label|None, expression text, literal value|None)]


@dataclass
class FacadeMethod:
    owner: str
    name: str
    file: Path
    line: int
    params: str
    emissions: list = field(default_factory=list)
    calls: list = field(default_factory=list)       # CallSite, outside the facade
    internal_calls: int = 0

    @property
    def events(self) -> list:
        return sorted({e.event for e in self.emissions if e.event})


@dataclass
class Model:
    root: Path
    files: list
    imports: dict                  # sdk label -> [files]
    emissions: list
    facades: dict                  # owner type -> [FacadeMethod]
    raw: list                      # Emission outside any facade
    bridges: list                  # emissions whose event name is dynamic (wrapper plumbing)
    config_calls: list             # (what, file, line)
    unresolved_wrappers: list = field(default_factory=list)
    enum_cases: dict = field(default_factory=dict)
    firebase_products: set = field(default_factory=set)

    def methods(self) -> list:
        return [m for ms in self.facades.values() for m in ms]

    def events(self, include_debug: bool = False) -> dict:
        """event name -> {"methods": [FacadeMethod], "raw": [Emission], "params": {key: set(kinds)}}

        Events that exist only in DEBUG builds are left out unless include_debug is set.
        """
        out: dict = {}
        for method in self.methods():
            for em in method.emissions:
                if not em.event or (em.debug and not include_debug):
                    continue
                entry = out.setdefault(em.event, {"methods": [], "raw": [], "params": {}})
                if method not in entry["methods"]:
                    entry["methods"].append(method)
                for p in em.params or []:
                    entry["params"].setdefault(p.key, set()).add(p.kind)
        for em in self.raw:
            if not em.event or (em.debug and not include_debug):
                continue
            entry = out.setdefault(em.event, {"methods": [], "raw": [], "params": {}})
            entry["raw"].append(em)
            for p in em.params or []:
                entry["params"].setdefault(p.key, set()).add(p.kind)
        return out


# -- value helpers -------------------------------------------------------------------------------

_BOUNDED_SUFFIXES = (".rawValue", ".analyticsName", ".analyticsResult", ".analyticsValue")
_UNBOUNDED_HINTS = (".localizedDescription", "String(describing", ".description", "\\(", ".name", ".title",
                    ".text", ".label", ".message", "error")


def infer_kind(expr: str, interpolated: bool = False) -> str:
    e = expr.strip()
    if re.fullmatch(r"-?\d+", e):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", e):
        return "double"
    if e in ("true", "false") or re.search(r"\?\s*(true|false)\s*:\s*(true|false)", e):
        return "bool"
    if re.search(r"\?\s*\d+\s*:\s*\d+", e):
        return "int"
    if e.startswith(('"', '#"')):
        return "string-dynamic" if interpolated or "\\(" in e else "string-literal"
    if e.endswith(_BOUNDED_SUFFIXES):
        return "string-enum"
    if re.search(r"\bInt\(|\.count\b|Count\b|\bInt64\(", e):
        return "int"
    if any(h in e for h in (".localizedDescription", "String(describing", ".description")):
        return "string-dynamic"
    if e.endswith((".name", ".title", ".text", ".label", ".message", ".identifier", ".id")):
        return "string-computed"
    return "unknown"


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _enum_string_values(files: list) -> dict:
    """{case: raw string} and {'Enum.case': raw string} for `enum X: String` declarations."""
    out: dict = {}
    for sf in files:
        for decl in sf.types:
            if not (decl.body and decl.body[1] is not None and re.search(r"\bString\b", decl.inherits)):
                continue
            if decl.keyword != "enum":
                continue
            lo, hi = decl.body
            for m in re.finditer(r"\bcase\b", sf.code[lo + 1:hi]):
                p = lo + 1 + m.end()
                eol = sf.code.find("\n", p)
                eol = hi if eol == -1 else min(eol, hi)
                items, start, i = [], p, p
                while i < eol:
                    ch = sf.code[i]
                    if ch in "([{":
                        i = sf.pairs.get(i, i) + 1
                        continue
                    if ch == ",":
                        items.append((start, i))
                        start = i + 1
                    i += 1
                items.append((start, eol))
                for a, b in items:
                    nm = re.match(r"\s*([A-Za-z_]\w*)", sf.code[a:b])
                    if not nm:
                        continue
                    name, raw = nm.group(1), nm.group(1)
                    eq = sf.code.find("=", a, b)
                    if eq != -1:
                        for q in range(eq, b):
                            if q in sf.strings:
                                raw = sf.strings[q].value
                                break
                    out[f"{decl.name}.{name}"] = raw
                    out.setdefault(name, raw)
    return out


def signature(params_text: str) -> list:
    """[(label, name, type)] of a function's parameters."""
    parts, depth, cur = [], 0, ""
    for ch in params_text:
        if ch in "([<{":
            depth += 1
        elif ch in ")]>}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    out = []
    for part in parts:
        head, _, rest = part.partition(":")
        words = head.split()
        if not words:
            continue
        label = None if words[0] == "_" else words[0]
        name = words[-1]
        type_ = re.sub(r"\s*=.*$", "", rest.strip(), flags=re.S).strip()
        out.append((label if len(words) > 1 or label else name, name, type_))
    return out


def refine_kinds(model: "Model") -> None:
    """Replace "unknown" parameter kinds using the facade method's own parameter types."""
    enum_cases: dict = {}
    for sf in model.files:
        for decl in sf.types:
            if decl.body and decl.body[1] is not None and decl.keyword == "enum":
                body = sf.code[decl.body[0] + 1:decl.body[1]]
                cases = re.findall(r"\bcase\s+([A-Za-z_]\w*)", body)
                enum_cases[decl.name] = len(cases)
    model.enum_cases = enum_cases
    for method in model.methods():
        sig = {name: type_ for _, name, type_ in signature(method.params)}
        for em in method.emissions:
            for prm in em.params or []:
                ident = re.match(r"^([A-Za-z_]\w*)(?:\.\w+)?$", prm.expr.strip())
                if not ident:
                    continue
                type_ = sig.get(ident.group(1), "").rstrip("?")
                if prm.kind in ("unknown", "string-enum") or prm.kind.startswith("string"):
                    if type_ == "String" and prm.expr.strip() == ident.group(1):
                        prm.kind = "string-param"
                    elif type_ in ("Int", "Int64", "Int32", "UInt"):
                        prm.kind = "int"
                    elif type_ == "Bool":
                        prm.kind = "bool"
                    elif type_ in ("Double", "Float", "CGFloat", "TimeInterval"):
                        prm.kind = "double"
                    elif type_ in enum_cases:
                        prm.kind = "string-enum"
                elif prm.kind == "unknown":
                    if type_ in enum_cases:
                        prm.kind = "string-enum"


# -- the scan ------------------------------------------------------------------------------------

def _param_names(params_text: str) -> list:
    """Internal parameter names of a function, in order."""
    names = []
    depth = 0
    cur = ""
    parts = []
    for ch in params_text:
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    for part in parts:
        head = part.split(":", 1)[0].split()
        names.append(head[-1] if head else "")
    return names


def _sink_for(call: sw.Call, sf: sw.SwiftFile, args: list) -> Sink | None:
    for sink in SINKS:
        if call.name != sink.name:
            continue
        if sink.receivers is not None:
            if call.receiver not in sink.receivers:
                continue
        elif sink.name_label is not None:
            if not args or args[0][0] != sink.name_label:
                continue
        elif sink.params_label is not None:
            if not any(label == sink.params_label for label, _, _ in args):
                continue
        return sink
    return None


def _arg_expr(sf: sw.SwiftFile, a: int, b: int) -> str:
    return _flatten(sf.text[a:b])


def _dict_params(sf: sw.SwiftFile, a: int, b: int, enums: dict):
    """Parameters from a dictionary literal expression text[a:b]; None if it is not one."""
    code = sf.code[a:b]
    stripped = code.strip()
    if stripped in ("nil", "[:]"):
        return []
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    open_i = a + code.index("[")
    close_i = sf.pairs.get(open_i)
    if close_i is None:
        return None
    params, start, i = [], open_i + 1, open_i + 1
    chunks = []
    while i < close_i:
        ch = sf.code[i]
        if ch in "([{":
            i = sf.pairs.get(i, i) + 1
            continue
        if ch == ",":
            chunks.append((start, i))
            start = i + 1
        i += 1
    if sf.code[start:close_i].strip():
        chunks.append((start, close_i))
    for ca, cb in chunks:
        colon = None
        j = ca
        while j < cb:
            c = sf.code[j]
            if c in "([{":
                j = sf.pairs.get(j, j) + 1
                continue
            if c == ":":
                colon = j
                break
            j += 1
        if colon is None:
            return None
        key_text = _flatten(sf.text[ca:colon])
        lit = sf.first_string_literal(ca, colon) if hasattr(sf, "first_string_literal") else sw.first_string(sf, ca, colon)
        if lit is not None:
            key = lit.value
        else:
            cm = FIREBASE_PARAM_CONSTANT.match(key_text)
            key = snake(cm.group(1)) if cm else f"<{key_text}>"
        expr = _arg_expr(sf, colon + 1, cb)
        vlit = sw.first_string(sf, colon + 1, cb)
        params.append(Param(key, expr, infer_kind(expr, bool(vlit and vlit.interpolated))))
    return params


def _event_name(sf: sw.SwiftFile, a: int, b: int, enums: dict):
    lit = sw.first_string(sf, a, b)
    expr = _arg_expr(sf, a, b)
    if lit is not None and not lit.interpolated:
        return lit.value, expr
    m = FIREBASE_CONSTANT.match(expr)
    if m:
        return snake(m.group(1)), expr
    m = re.fullmatch(r"(?:([A-Za-z_]\w*)\s*)?\.\s*([A-Za-z_]\w*)(?:\s*\.\s*rawValue)?", expr)
    if m:
        qualified = f"{m.group(1)}.{m.group(2)}" if m.group(1) else None
        if qualified and qualified in enums:
            return enums[qualified], expr
        if m.group(2) in enums:
            return enums[m.group(2)], expr
    return None, expr


def debug_ranges(text: str) -> list:
    """Offset ranges of text that is compiled only in DEBUG builds (`#if DEBUG` ... `#else`/`#endif`)."""
    ranges, stack, pos = [], [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#if"):
            is_debug = re.match(r"#if\s+DEBUG\b(?!\s*\|\|)", stripped) is not None and "!" not in stripped
            stack.append([is_debug, pos + len(line) if is_debug else None])
        elif stripped.startswith("#else") or stripped.startswith("#elseif"):
            if stack and stack[-1][0] and stack[-1][1] is not None:
                ranges.append((stack[-1][1], pos))
                stack[-1][1] = None
        elif stripped.startswith("#endif"):
            if stack:
                is_debug, start = stack.pop()
                if is_debug and start is not None:
                    ranges.append((start, pos))
        pos += len(line)
    return ranges


def scan(root: Path, exclude: tuple = ()) -> Model:
    root = Path(root)
    files = []
    for path in sw.iter_swift_files(root):
        rel = path.relative_to(root).as_posix()
        if any(part in rel for part in exclude):
            continue
        files.append(sw.parse(path))
    enums = _enum_string_values(files)
    calls_by_file = {sf.path: sw.calls_in(sf, 0, len(sf.code)) for sf in files}
    all_func_names: dict = {}
    for sf in files:
        for fn in sf.funcs:
            all_func_names.setdefault(fn.name, []).append((sf.path, fn.owner))

    imports: dict = {}
    for sf in files:
        for m in re.finditer(r"^\s*(?:@\w+\s+)*import\s+(?:struct\s+|class\s+|enum\s+)?([A-Za-z_]\w*)", sf.code, re.M):
            label = IMPORT_SDKS.get(m.group(1))
            if label:
                imports.setdefault(label, []).append(sf.path)

    # 1. wrappers: functions that pass their own name parameter to a sink or a handler
    wrappers: dict = {}          # (owner, name) -> (name_param_index, params_param_name)
    for sf in files:
        for fn in sf.funcs:
            if not fn.body or fn.body[1] is None or fn.name in ("body", "init"):
                continue
            pnames = _param_names(fn.params)
            if not pnames:
                continue
            for call in sw.calls_in(sf, fn.body[0], fn.body[1]):
                args = sw.split_args(sf, call.open_paren)
                sink = _sink_for(call, sf, args)
                is_handler = bool(HANDLER_NAME.match(call.name))
                if not sink and not is_handler:
                    continue
                if not args:
                    continue
                idx = 0
                if sink and sink.name_label:
                    idx = next((i for i, (lab, _, _) in enumerate(args) if lab == sink.name_label), 0)
                first = _arg_expr(sf, args[idx][1], args[idx][2])
                if first in pnames:
                    wrappers[(fn.owner, fn.name)] = pnames.index(first)
                    break

    debug_by_file = {sf.path: debug_ranges(sf.text) for sf in files}

    # 2. emissions: SDK calls with a literal/enum name, and wrapper calls
    emissions: list = []
    bridges: list = []
    config_calls: list = []
    for sf in files:
        for call in calls_by_file[sf.path]:
            args = sw.split_args(sf, call.open_paren)
            line = sf.line(call.start)
            fn = sf.func_at(call.start)
            owner_decl = sf.type_at(call.start)
            owner = owner_decl.name if owner_decl else None
            fname = fn.name if fn else None
            fparams = fn.params if fn else ""

            if call.receiver == "Analytics" and call.name in (
                    "setUserID", "setUserProperty", "setDefaultEventParameters", "setAnalyticsCollectionEnabled",
                    "setConsent", "resetAnalyticsData", "setSessionTimeoutInterval"):
                detail = ""
                if call.name == "setDefaultEventParameters" and args:
                    ps = _dict_params(sf, args[0][1], args[0][2], enums) or []
                    detail = ", ".join(f"{p.key} = {p.expr}" for p in ps)
                elif args:
                    detail = _arg_expr(sf, args[0][1], args[0][2])
                config_calls.append((call.name, sf.path, line, detail))
            elif call.name in ("setUserId", "setUserID", "identify", "setUserProperty") and call.receiver != "Analytics":
                config_calls.append((call.name, sf.path, line, ""))

            sink = _sink_for(call, sf, args)
            via = ""
            name_idx = None
            params_expr = None
            label = sink.sdk if sink else None
            if sink:
                name_idx = 0
                if sink.name_label:
                    name_idx = next((i for i, (lab, _, _) in enumerate(args) if lab == sink.name_label), 0)
                pl = next((a for a in args if a[0] == sink.params_label), None)
                params_expr = pl[1:] if pl else None
            else:
                for (w_owner, w_name), idx in wrappers.items():
                    if call.name != w_name:
                        continue
                    same_type = owner == w_owner
                    qualified = call.receiver.split(".")[0] == w_owner if call.receiver else False
                    if same_type and call.receiver in ("", "self") or qualified:
                        name_idx, via, label = idx, w_name, "via " + w_name
                        # parameters: the argument right after the name, labelled or not
                        rest = [a for i, a in enumerate(args) if i != idx]
                        if rest:
                            params_expr = rest[0][1:]
                        break
            if name_idx is None or name_idx >= len(args):
                continue
            ev, ev_expr = _event_name(sf, args[name_idx][1], args[name_idx][2], enums)
            params = _dict_params(sf, params_expr[0], params_expr[1], enums) if params_expr else []
            em = Emission(ev, ev_expr, params, label or "", sf.path, line, owner, fname, fparams, via, call.start,
                          any(a <= call.start < b for a, b in debug_by_file[sf.path]))
            if ev is None and (re.fullmatch(r"[A-Za-z_]\w*", ev_expr) or (fn is not None and (owner, fname) in wrappers)):
                bridges.append(em)        # plumbing: the name is a variable, not an event of this app
                continue
            emissions.append(em)

    # 3. facades: functions that emit directly
    by_func: dict = {}
    for em in emissions:
        by_func.setdefault((em.file, em.owner, em.func), []).append(em)
    facades: dict = {}
    raw: list = []
    emitter_count: dict = {}
    for (path, owner, func), ems in by_func.items():
        emitter_count[owner] = emitter_count.get(owner, 0) + 1
    name_hint = re.compile(r"(?i)analytic|telemetry|tracker|tracking|metric|event")
    for sf in files:
        for fn in sf.funcs:
            ems = by_func.get((sf.path, fn.owner, fn.name))
            if not ems or fn.name in ("body",):
                continue
            is_facade = fn.owner is not None and (emitter_count.get(fn.owner, 0) >= 3 or name_hint.search(fn.owner))
            if is_facade:
                facades.setdefault(fn.owner, []).append(
                    FacadeMethod(fn.owner, fn.name, sf.path, sf.line(fn.start), _flatten(fn.params), ems))
            else:
                raw.extend(ems)

    # 4. call sites of facade methods
    for owner, methods in facades.items():
        for method in methods:
            unique = len(all_func_names.get(method.name, [])) == 1
            for sf in files:
                for call in calls_by_file[sf.path]:
                    if call.name != method.name:
                        continue
                    decl_owner = sf.type_at(call.start)
                    here = decl_owner.name if decl_owner else None
                    first = call.receiver.split(".")[0] if call.receiver else ""
                    exact = first == owner or (here == owner and call.receiver in ("", "self"))
                    if not exact and not unique:
                        continue
                    if exact and sf.path == method.file and here == owner:
                        method.internal_calls += 1
                        continue
                    fn = sf.func_at(call.start)
                    call_args = []
                    for lab, a, b in sw.split_args(sf, call.open_paren):
                        lit = sw.first_string(sf, a, b)
                        call_args.append((lab, _arg_expr(sf, a, b), lit.value if lit and not lit.interpolated else None))
                    method.calls.append(CallSite(sf.path, sf.line(call.start), here, fn.name if fn else None,
                                                 call.receiver, "exact" if exact else "by name", call.start,
                                                 call_args))
    model = Model(root, files, imports, emissions, facades, raw, bridges, config_calls)
    refine_kinds(model)
    model.firebase_products = find_firebase_products(root)
    return model


def find_firebase_products(root: Path) -> set:
    """Firebase Analytics products linked by the project (FirebaseAnalytics, FirebaseAnalyticsCore, ...)."""
    import os
    found: set = set()
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in sw.EXCLUDED_DIRS]
        for name in files:
            if name == "project.pbxproj" or name == "Package.swift":
                try:
                    text = (Path(folder) / name).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                found.update(re.findall(r"productName = (FirebaseAnalytics\w*);", text))
                found.update(re.findall(r'\.product\(\s*name:\s*"(FirebaseAnalytics\w*)"', text))
    return found


def vocabulary(method: FacadeMethod, param_name: str) -> dict:
    """What callers pass for one parameter of a facade method.

    {"literals": {value: count}, "dynamic": [expr, ...], "calls": n}
    """
    sig = signature(method.params)
    index = next((i for i, (lab, name, _) in enumerate(sig) if name == param_name), None)
    label = next((lab for lab, name, _ in sig if name == param_name), None)
    literals: dict = {}
    dynamic: list = []
    for call in method.calls:
        chosen = None
        if label is not None:
            chosen = next((a for a in call.args if a[0] == label), None)
        if chosen is None and index is not None and index < len(call.args) and call.args[index][0] in (None, label):
            chosen = call.args[index]
        if chosen is None:
            continue
        _, expr, literal = chosen
        if literal is not None:
            literals[literal] = literals.get(literal, 0) + 1
        else:
            dynamic.append(expr)
    return {"literals": literals, "dynamic": dynamic, "calls": len(method.calls)}


# -- plan documents ----------------------------------------------------------------------------

_BACKTICK = re.compile(r"`([^`]+)`")


def parse_plan(path: Path) -> dict:
    """Events listed in markdown tables whose header has an Event/Evento column.

    Returns {event: {"params": [names], "line": n, "section": heading}}. The
    event is the first backticked name of the first cell; parameters are the
    backticked names of the second cell (a note in parentheses is ignored).
    """
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    plan: dict = {}
    section = ""
    in_table = False
    for number, line in enumerate(lines, 1):
        heading = re.match(r"^#{1,6}\s+(.*)", line)
        if heading:
            section = heading.group(1).strip()
        if not line.strip().startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:
            if cells and re.match(r"(?i)^(event|evento|event name)$", cells[0].strip("`* ")):
                in_table = True
            continue
        if cells and set(cells[0]) <= set("-: "):
            continue
        names = _BACKTICK.findall(cells[0]) if cells else []
        if not names:
            continue
        params = []
        if len(cells) > 1:
            params = [p.split("=")[0].strip() for p in _BACKTICK.findall(re.sub(r"\([^)]*\)", "", cells[1]))]
        plan[names[0]] = {"params": params, "line": number, "section": section}
    return plan
