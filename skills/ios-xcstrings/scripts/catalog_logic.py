"""What to check in a String Catalog, what to translate, and how to apply it.

Pure functions over the structures in xcstrings.py: no printing, no argument
parsing. catalog.py is the command line on top.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import xcstrings as xc

EXCLUDED_DIRS = {".git", ".build", "DerivedData", "Pods", "Carthage", "node_modules", ".claude", "build",
                 ".swiftpm", "xcuserdata"}
TRANSLATED, NEW, NEEDS_REVIEW, MISSING = "translated", "new", "needs_review", "missing"
STATUSES = (TRANSLATED, NEW, NEEDS_REVIEW, MISSING)
PLURAL_NAMES = ("zero", "one", "two", "few", "many", "other")
_IDENTIFIER_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+$")


# -- finding files -----------------------------------------------------------------------------

def find_catalogs(root: Path) -> list[Path]:
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.endswith(".xcassets"))
        found.extend(Path(folder) / f for f in sorted(files) if f.endswith(".xcstrings"))
    return found


def project_root(catalog_path: Path) -> Path:
    """The closest folder above the catalog that looks like a project root."""
    for folder in [catalog_path.resolve().parent, *catalog_path.resolve().parents]:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        if any(n.endswith(".xcodeproj") or n in ("Package.swift", ".git") for n in names):
            return folder
    return catalog_path.resolve().parent


def read_sources(root: Path) -> str:
    chunks = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in files:
            if name.endswith(".swift"):
                try:
                    chunks.append((Path(folder) / name).read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    return "\n".join(chunks)


# -- one key -----------------------------------------------------------------------------------

def referenced_in(key: str, sources: str) -> bool:
    """Does the key appear in the Swift sources as a string literal?

    A key with format specifiers is the extracted form of an interpolated
    literal ("V \\(version)" is the key "V %@"), and "%%" is a literal percent
    sign, so those are matched too.
    """
    body = xc.swift_literal_body(key)
    plain = body.replace("%%", "%")
    if f'"{body}"' in sources or f'"{plain}"' in sources:
        return True
    if not xc.parse_specs(key):
        return False
    pattern, last = "", 0
    for m in xc._SPEC.finditer(plain):
        pattern += re.escape(plain[last:m.start()]) + r"\\\(.*?\)"
        last = m.end()
    pattern += re.escape(plain[last:])
    return re.search('"' + pattern + '"', sources) is not None


@dataclass
class KeyInfo:
    key: str
    entry: dict
    kind: str                 # "text" | "plural" | "complex"
    source_text: str | None   # the text, for kind "text"
    source_forms: dict | None
    source_is_key: bool
    stale: bool
    skip: str | None = None   # why it is not translated: should_not_translate, no_text, stale_unreferenced

    @property
    def comment(self) -> str | None:
        value = self.entry.get("comment")
        return value if isinstance(value, str) else None

    @property
    def compare_text(self) -> str | None:
        """The text other languages are compared against (the plural 'other' form for plurals)."""
        if self.kind == "text":
            return self.source_text
        if self.kind == "plural":
            return self.source_forms.get("other") or next(iter(self.source_forms.values()), None)
        return None


def key_info(catalog: xc.Catalog, key: str, entry: dict, sources: str | None = None) -> KeyInfo:
    entry = entry if isinstance(entry, dict) else {}
    loc = (entry.get("localizations") or {}).get(catalog.source_language)
    forms = xc.plural_forms(loc)
    text = xc.plain_value(loc)
    if forms is not None:
        kind, source_text, source_forms, from_key = "plural", None, forms, False
    elif text is not None:
        kind, source_text, source_forms, from_key = "text", text, None, False
    elif loc is not None and list(xc.iter_units(loc)):
        kind, source_text, source_forms, from_key = "complex", None, None, False
    else:
        kind, source_text, source_forms, from_key = "text", key, None, True
    info = KeyInfo(key, entry, kind, source_text, source_forms, from_key,
                   stale=entry.get("extractionState") == "stale")
    if entry.get("shouldTranslate") is False:
        info.skip = "should_not_translate"
    elif kind != "complex" and not xc.has_letters(info.compare_text or ""):
        info.skip = "no_text"
    elif info.stale and sources is not None and not referenced_in(key, sources):
        info.skip = "stale_unreferenced"
    return info


def _units(loc) -> list[tuple[tuple, dict]]:
    return list(xc.iter_units(loc)) if isinstance(loc, dict) else []


def locale_status(info: KeyInfo, locale: str) -> str:
    loc = (info.entry.get("localizations") or {}).get(locale)
    units = _units(loc)
    if not units:
        return MISSING
    if any(not str(u.get("value", "")).strip() for _, u in units):
        return MISSING
    states = {u.get("state", TRANSLATED) for _, u in units}
    if NEW in states:
        return NEW
    if NEEDS_REVIEW in states:
        return NEEDS_REVIEW
    return TRANSLATED


# -- audit -------------------------------------------------------------------------------------

@dataclass
class Finding:
    severity: str            # error | warning | info
    code: str
    message: str
    locale: str | None = None
    details: list[str] = field(default_factory=list)   # one line per key


@dataclass
class AuditResult:
    path: str
    source_language: str
    version: str
    key_count: int
    translatable: int
    skipped: dict
    locales: list[str]
    coverage: dict           # locale -> {status: count}
    findings: list[Finding]
    writable: bool
    writable_note: str
    scanned_sources: bool

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def _short(text: str, limit: int = 60) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def audit(catalog: xc.Catalog, locales: list[str] | None = None, sources: str | None = None,
          compile_check: bool = False) -> AuditResult:
    source_language = catalog.source_language
    wanted = [l for l in (locales if locales else catalog.locales()) if l != source_language]
    infos = [key_info(catalog, k, e, sources) for k, e in catalog.strings.items()]
    skipped = {"should_not_translate": 0, "no_text": 0, "stale_unreferenced": 0}
    for info in infos:
        if info.skip:
            skipped[info.skip] += 1
    live = [i for i in infos if not i.skip]
    findings: list[Finding] = []

    for key in catalog.duplicates:
        findings.append(Finding("error", "duplicate_key",
                                f"the key {key!r} appears more than once in the file; the later copy silently wins"))

    if compile_check:
        ok, message = xc.compile_check(catalog.path)
        if ok is False:
            findings.append(Finding("error", "xcode_rejects",
                                    "Xcode's compiler (xcstringstool) cannot read this catalog", None, [message]))

    coverage = {l: {s: 0 for s in STATUSES} for l in wanted}
    for locale in wanted:
        by_issue: dict[str, list[str]] = {}
        for info in live:
            status = locale_status(info, locale)
            coverage[locale][status] += 1
            if status != TRANSLATED:
                shown = info.compare_text or info.key
                by_issue.setdefault(status, []).append(
                    info.key if shown == info.key else f"{info.key}  <- {_short(shown)}")
            if status == MISSING:
                continue
            loc = info.entry["localizations"][locale]
            _check_localization(info, locale, loc, by_issue)
        _emit_locale_findings(findings, locale, by_issue)

    # Text-less keys ("%@") are not counted, but a translation of one still has to keep its specifiers.
    for info in infos:
        if info.skip == "no_text":
            for locale in wanted:
                loc = (info.entry.get("localizations") or {}).get(locale)
                if isinstance(loc, dict) and info.compare_text:
                    issues: dict[str, list[str]] = {}
                    _check_localization(info, locale, loc, issues)
                    for code in ("placeholder_mismatch", "percent_space"):
                        for row in issues.get(code, []):
                            _extra_issue(findings, locale, code, row)

    # Identifier-like keys with no source value: the UI shows the key unless the code supplies one.
    bare = [i.key for i in live if i.source_is_key and _IDENTIFIER_KEY.match(i.key)]
    if bare:
        findings.append(Finding("warning", "no_source_value",
                                f"{len(bare)} identifier-like key(s) have no {source_language} value, so the app "
                                "shows the key itself unless the code passes a default", None,
                                [k for k in bare]))
    stale = [i for i in infos if i.stale]
    if stale:
        if sources is None:
            findings.append(Finding("info", "stale", f"{len(stale)} key(s) are marked stale by Xcode (source not "
                                    "scanned, so they are counted as in use)"))
        else:
            used = [i for i in stale if not i.skip]
            unused = [i for i in stale if i.skip == "stale_unreferenced"]
            if used:
                findings.append(Finding(
                    "info", "stale_in_use",
                    f"{len(used)} stale key(s) appear as string literals in the Swift sources: Xcode cannot see "
                    "a key that is passed around in a variable, so these are most likely still used. Do not delete "
                    "them because of the stale mark", None, [i.key for i in used]))
            if unused:
                findings.append(Finding(
                    "warning", "stale_unreferenced",
                    f"{len(unused)} stale key(s) have no literal match in the Swift sources: candidates for "
                    "deletion in Xcode, after checking they are not built dynamically", None,
                    [i.key for i in unused]))
    if skipped["no_text"]:
        findings.append(Finding("info", "no_text", f"{skipped['no_text']} key(s) have nothing to translate "
                                "(symbols, numbers or only format specifiers); not counted"))
    if not catalog.writable:
        findings.append(Finding("info", "not_writable",
                                f"this tool will not edit the file: {catalog.not_writable_reason}"))
    unsupported = [i.key for i in live if i.kind == "complex"]
    if unsupported:
        findings.append(Finding("info", "complex_source", f"{len(unsupported)} key(s) use device variations or "
                                "substitutions; they are counted but this tool does not write them", None,
                                unsupported))

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f.severity])
    return AuditResult(str(catalog.path), source_language, catalog.version, len(catalog.strings), len(live),
                       skipped, wanted, coverage, findings, catalog.writable,
                       "" if catalog.writable else catalog.not_writable_reason, sources is not None)


def _check_localization(info: KeyInfo, locale: str, loc: dict, by_issue: dict) -> None:
    source = info.compare_text
    if source is None:
        return
    needs = xc.plural_categories(locale)
    forms = xc.plural_forms(loc)
    if forms is not None:
        missing = [c for c in needs if c not in forms]
        if missing:
            by_issue.setdefault("plural_incomplete", []).append(
                f"{info.key}  needs {', '.join(missing)} for {locale}")
    elif info.kind == "plural" and len(needs) > 1 and xc.plain_value(loc) is not None:
        by_issue.setdefault("plural_incomplete", []).append(
            f"{info.key}  is a single string but {locale} has {len(needs)} plural forms")
    for path, unit in _units(loc):
        value = unit.get("value")
        if not isinstance(value, str):
            continue
        if path and path[0] == "plural" and len(path) == 2:
            compare = (info.source_forms or {}).get(path[1]) if info.kind == "plural" else None
            compare = compare if compare is not None else source
            allow_missing = path[1] != "other"
        elif path:
            continue
        else:
            compare, allow_missing = source, False
        for problem in xc.compare_specs(compare, value, allow_missing):
            by_issue.setdefault("placeholder_mismatch", []).append(f"{info.key}  {problem}")
        for raw in xc.percent_lookalikes(value):
            by_issue.setdefault("percent_space", []).append(f"{info.key}  contains {raw!r}")
        if unit.get("state", TRANSLATED) == NEEDS_REVIEW or path:
            continue
        if value != compare and value.strip() == compare.strip():
            continue
        if value[:1].isspace() != compare[:1].isspace() or value[-1:].isspace() != compare[-1:].isspace():
            by_issue.setdefault("whitespace_differs", []).append(
                f"{info.key}  {compare!r} -> {value!r}")
        if value.count("\n") != compare.count("\n"):
            by_issue.setdefault("newline_differs", []).append(
                f"{info.key}  {compare.count(chr(10))} line break(s) in the source, {value.count(chr(10))} here")
        if value == compare and sum(ch.isalpha() for ch in compare) >= 4 and not info.source_is_key \
                and _translated_elsewhere(info, locale, compare):
            by_issue.setdefault("identical_to_source", []).append(f"{info.key}  {_short(value)}")
        elif len(value) >= 14 and len(value) >= 1.8 * len(compare) and len(value) - len(compare) >= 8:
            by_issue.setdefault("much_longer", []).append(
                f"{info.key}  {len(compare)} -> {len(value)} characters")


def _translated_elsewhere(info: KeyInfo, locale: str, source: str) -> bool:
    """A word that stays the same in every language of its script is a name, not a missed translation."""
    if not xc.uses_latin_script(locale):
        return True
    for other, loc in (info.entry.get("localizations") or {}).items():
        if other == locale or other == info.entry.get("_source") or not xc.uses_latin_script(other):
            continue
        value = xc.plain_value(loc)
        if value is not None and value != source and value.strip():
            return True
    return False


_LOCALE_MESSAGES = {
    "placeholder_mismatch": ("error", "format specifiers do not match the source (crash or wrong text at runtime)"),
    "percent_space": ("warning", "a percent sign followed by a space and a letter, which a format string reads as "
                                 "a placeholder; a literal percent sign is written %%"),
    MISSING: ("warning", "no translation"),
    NEW: ("warning", "state is 'new': Xcode has not seen a translation"),
    NEEDS_REVIEW: ("warning", "flagged 'needs review'"),
    "plural_incomplete": ("warning", "plural forms incomplete for the language"),
    "whitespace_differs": ("warning", "leading or trailing whitespace differs from the source"),
    "newline_differs": ("warning", "number of line breaks differs from the source"),
    "identical_to_source": ("info", "identical to the source (untranslated, or a name that stays)"),
    "much_longer": ("info", "much longer than the source (risk of truncation in the layout)"),
}


def _extra_issue(findings: list[Finding], locale: str, code: str, row: str) -> None:
    for f in findings:
        if f.code == code and f.locale == locale:
            f.details.append(row)
            f.message = f"{len(f.details)} key(s): {_LOCALE_MESSAGES[code][1]}"
            return
    severity, text = _LOCALE_MESSAGES[code]
    findings.append(Finding(severity, code, f"1 key(s): {text}", locale, [row]))


def _emit_locale_findings(findings: list[Finding], locale: str, by_issue: dict) -> None:
    for code, (severity, text) in _LOCALE_MESSAGES.items():
        rows = by_issue.get(code)
        if rows:
            findings.append(Finding(severity, code, f"{len(rows)} key(s): {text}", locale, rows))


def audit_to_dict(result: AuditResult) -> dict:
    return {
        "catalog": result.path, "source_language": result.source_language, "version": result.version,
        "keys": result.key_count, "translatable": result.translatable, "skipped": result.skipped,
        "writable": result.writable, "writable_note": result.writable_note,
        "coverage": result.coverage,
        "findings": [{"severity": f.severity, "code": f.code, "locale": f.locale, "message": f.message,
                      "details": f.details} for f in result.findings],
    }


# -- work list ---------------------------------------------------------------------------------

def _prefix(key: str) -> str:
    for sep in ("_", "."):
        if sep in key:
            return key.split(sep, 1)[0]
    return ""


def todo(catalog: xc.Catalog, locale: str, include=(MISSING, NEW, NEEDS_REVIEW), examples: int = 0,
         sources: str | None = None, limit: int | None = None, reference: list[str] | None = None) -> dict:
    if locale == catalog.source_language:
        raise xc.CatalogError(f"{locale} is the source language of this catalog; there is nothing to translate into it")
    infos = [key_info(catalog, k, e, sources) for k, e in catalog.strings.items()]
    items, pending_prefixes, unsupported = [], set(), []
    for info in infos:
        if info.skip:
            continue
        status = locale_status(info, locale)
        if status not in include:
            continue
        if info.kind == "complex":
            unsupported.append(info.key)
            continue
        item = {"key": info.key, "reason": status, "source": info.source_text if info.kind == "text"
                else info.source_forms}
        if info.comment:
            item["comment"] = info.comment
        if info.kind == "plural":
            item["plural_categories"] = list(xc.plural_categories(locale))
        specs = [s.raw for s in xc.parse_specs(info.compare_text or "")]
        if specs:
            item["placeholders"] = specs
        if status in (NEW, NEEDS_REVIEW):
            current = xc.plain_value((info.entry.get("localizations") or {}).get(locale))
            forms = xc.plural_forms((info.entry.get("localizations") or {}).get(locale))
            if current is not None or forms is not None:
                item["current"] = current if current is not None else forms
        if info.stale:
            item["stale"] = True
        refs = {}
        for ref in reference or []:
            loc = (info.entry.get("localizations") or {}).get(ref)
            value = xc.plural_forms(loc) or xc.plain_value(loc)
            if value and locale_status(info, ref) == TRANSLATED:
                refs[ref] = value
        if refs:
            item["reference"] = refs
        items.append(item)
        pending_prefixes.add(_prefix(info.key))
    total = len(items)
    if limit is not None:
        items = items[:limit]
    result = {
        "catalog": str(catalog.path), "source_language": catalog.source_language, "language": locale,
        "total": total, "items": items,
        "rules": [
            "Keep every format specifier (%@, %lld, %1$@, %.1f ...) exactly; reorder only with positional "
            "specifiers such as %2$@.",
            "Keep line breaks (\\n) and leading or trailing spaces as in the source.",
            "For a plural item give one text per category in plural_categories, as an object.",
            "Do not translate product, brand or feature names unless the existing translations already do.",
        ],
    }
    if unsupported:
        result["not_handled"] = {"reason": "device variations or substitutions: edit in Xcode", "keys": unsupported}
    if examples:
        result["examples"] = _examples(catalog, infos, locale, examples, pending_prefixes)
    return result


def _examples(catalog, infos, locale, count, prefixes) -> list[dict]:
    pairs = []
    for info in infos:
        if info.skip or info.kind != "text":
            continue
        loc = (info.entry.get("localizations") or {}).get(locale)
        value = xc.plain_value(loc)
        if value is None or locale_status(info, locale) != TRANSLATED or value == info.source_text:
            continue
        pairs.append((info, value))
    near = [p for p in pairs if _prefix(p[0].key) in prefixes and _prefix(p[0].key)]
    rest = [p for p in pairs if p not in near]
    chosen = near[:count]
    if len(chosen) < count and rest:
        step = max(1, len(rest) // (count - len(chosen)))
        chosen += rest[::step][: count - len(chosen)]
    return [{"key": i.key, "source": i.source_text, "translation": v, **({"comment": i.comment} if i.comment else {})}
            for i, v in chosen]


# -- applying translations ---------------------------------------------------------------------

@dataclass
class Change:
    key: str
    before: object      # None, str or {category: str}
    after: object
    status_before: str


@dataclass
class ApplyPlan:
    language: str
    state: str
    changes: list[Change] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _current_value(loc):
    forms = xc.plural_forms(loc)
    return forms if forms is not None else xc.plain_value(loc)


def plan_apply(catalog: xc.Catalog, payload: dict, state: str = NEEDS_REVIEW, overwrite: bool = False,
               language: str | None = None, sources: str | None = None) -> ApplyPlan:
    locale = language or payload.get("language")
    plan = ApplyPlan(str(locale), state)
    if not isinstance(locale, str) or not locale:
        plan.problems.append("the file has no 'language' (and --language was not given)")
        return plan
    if locale == catalog.source_language:
        plan.problems.append(f"{locale} is the source language; its text is edited in the code or in Xcode")
        return plan
    translations = payload.get("translations")
    if not isinstance(translations, dict) or not translations:
        plan.problems.append("the file has no 'translations' object")
        return plan
    if state not in (TRANSLATED, NEEDS_REVIEW):
        plan.problems.append(f"state must be 'translated' or 'needs_review', not {state!r}")
        return plan
    if not catalog.writable:
        plan.problems.append(f"this catalog cannot be written safely: {catalog.not_writable_reason}")
        return plan
    needs = xc.plural_categories(locale)
    for key, value in translations.items():
        entry = catalog.strings.get(key)
        if entry is None:
            plan.problems.append(f"{key!r}: no such key in the catalog")
            continue
        info = key_info(catalog, key, entry, sources)
        if info.skip == "should_not_translate":
            plan.problems.append(f"{key!r}: marked 'Don't translate'")
            continue
        if info.kind == "complex":
            plan.problems.append(f"{key!r}: has device variations or substitutions; edit it in Xcode")
            continue
        loc = (entry.get("localizations") or {}).get(locale)
        reason = xc.unsupported_reason(loc)
        if reason:
            plan.problems.append(f"{key!r}: the {locale} localization {reason}; edit it in Xcode")
            continue
        problems_before = len(plan.problems)
        if isinstance(value, str):
            texts = {"": value}
        elif isinstance(value, dict) and value:
            texts = dict(value)
            bad = [c for c in texts if c not in PLURAL_NAMES]
            if bad:
                plan.problems.append(f"{key!r}: unknown plural categories {bad}")
            absent = [c for c in needs if c not in texts]
            if absent:
                plan.problems.append(f"{key!r}: {locale} needs the plural forms {', '.join(needs)}; "
                                     f"missing {', '.join(absent)}")
        else:
            plan.problems.append(f"{key!r}: the translation must be a string, or an object of plural forms")
            continue
        for category, text in texts.items():
            label = key if not category else f"{key} [{category}]"
            if not isinstance(text, str) or not text.strip():
                plan.problems.append(f"{label!r}: the translation is empty")
                continue
            compare = info.compare_text
            if category and info.kind == "plural":
                compare = info.source_forms.get(category, compare)
            for problem in xc.compare_specs(compare or "", text, allow_missing=bool(category) and category != "other"):
                plan.problems.append(f"{label!r}: {problem}")
        if len(plan.problems) > problems_before:
            continue
        status = locale_status(info, locale)
        before = _current_value(loc)
        after = texts[""] if isinstance(value, str) else texts
        if status == TRANSLATED and before == after and _state_of(loc) == state:
            plan.unchanged.append(key)
            continue
        if status == TRANSLATED and before is not None and before != after and not overwrite:
            plan.problems.append(f"{key!r}: already translated; pass --overwrite to replace it")
            continue
        plan.changes.append(Change(key, before, after, status))
    return plan


def _state_of(loc) -> str | None:
    states = {u.get("state") for _, u in _units(loc)}
    return states.pop() if len(states) == 1 else None


def _set_key(container: dict, key: str, value) -> None:
    """Set a key, inserting it in sorted position when the container's keys are already sorted."""
    if key in container:
        container[key] = value
        return
    keys = list(container)
    items = list(container.items())
    if keys == sorted(keys):
        position = next((i for i, k in enumerate(keys) if k > key), len(keys))
    else:
        position = len(keys)
    items.insert(position, (key, value))
    container.clear()
    container.update(items)


def _unit(text: str, state: str, previous: dict | None = None) -> dict:
    unit = dict(previous) if isinstance(previous, dict) else {}
    unit["state"] = state
    unit["value"] = text
    if list(unit) != sorted(unit):
        unit = {k: unit[k] for k in sorted(unit)}
    return {"stringUnit": unit}


def apply_plan(catalog: xc.Catalog, plan: ApplyPlan) -> dict:
    """The catalog data with the plan's changes applied; the original is left alone."""
    data = copy.deepcopy(catalog.data)
    for change in plan.changes:
        entry = data["strings"][change.key]
        locs = entry.get("localizations")
        if not isinstance(locs, dict):
            locs = {}
            _set_key(entry, "localizations", locs)
        previous = locs.get(plan.language) or {}
        if isinstance(change.after, str):
            old = previous.get("stringUnit") if isinstance(previous, dict) else None
            new_loc = _unit(change.after, plan.state, old)
            existing = locs.get(plan.language)
            if isinstance(existing, dict) and not existing.get("variations") and set(existing) <= {"stringUnit"}:
                existing["stringUnit"] = new_loc["stringUnit"]
            else:
                _set_key(locs, plan.language, new_loc)
        else:
            old_variations = (previous.get("variations") or {}).get("plural", {}) if isinstance(previous, dict) else {}
            plural = {}
            for category in sorted(change.after):
                plural[category] = _unit(change.after[category], plan.state,
                                         (old_variations.get(category) or {}).get("stringUnit"))
            _set_key(locs, plan.language, {"variations": {"plural": plural}})
    return data
