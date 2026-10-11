"""Reading, checking and writing Xcode String Catalogs (.xcstrings).

A catalog is JSON, but Xcode is picky about how it is written (a space on both
sides of the colon, `{` blank line `}` for an empty object, sorted keys, whether
the file ends in a newline), and a tool that re-serializes it with a generic
JSON writer turns a two-key change into a diff of the whole file. So this
module never "pretty prints" a catalog; it re-emits it in the style the file
already has, and refuses to write at all unless emitting the parsed data
reproduces the original bytes exactly (the round-trip guard).

It also holds what the checks need to understand a catalog: the units of text
inside a localization (plain, plural and device variations, substitutions),
format specifiers and how two strings' specifiers compare, and the plural
categories each language needs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


class CatalogError(Exception):
    """A catalog could not be read, or may not be written safely."""


# -- the file format --------------------------------------------------------------------------

INDENT = "  "


@dataclass(frozen=True)
class Style:
    """The formatting choices a catalog file makes that Xcode versions differ on."""
    empty_object: str = "blank"   # "blank": {, blank line, }   "compact": {}
    empty_array: str = "blank"
    escape_slash: bool = False    # Foundation escapes "/" as "\/" unless told not to
    trailing_newline: bool = True


def _escape(text: str, escape_slash: bool) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "/" and escape_slash:
            out.append("\\/")
        elif code < 0x20:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _emit(value, depth: int, style: Style) -> str:
    pad = INDENT * depth
    if isinstance(value, dict):
        if not value:
            return "{}" if style.empty_object == "compact" else "{\n\n" + pad + "}"
        rows = [f"{pad}{INDENT}{_escape(k, style.escape_slash)} : {_emit(v, depth + 1, style)}"
                for k, v in value.items()]
        return "{\n" + ",\n".join(rows) + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]" if style.empty_array == "compact" else "[\n\n" + pad + "]"
        rows = [f"{pad}{INDENT}{_emit(v, depth + 1, style)}" for v in value]
        return "[\n" + ",\n".join(rows) + "\n" + pad + "]"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return _escape(value, style.escape_slash)
    return json.dumps(value)


def dumps(data: dict, style: Style = Style()) -> str:
    return _emit(data, 0, style) + ("\n" if style.trailing_newline else "")


@dataclass
class Catalog:
    path: Path
    data: dict
    text: str
    style: Style
    roundtrip_ok: bool
    roundtrip_note: str = ""
    duplicates: list[str] = field(default_factory=list)

    @property
    def strings(self) -> dict:
        return self.data["strings"]

    @property
    def source_language(self) -> str:
        return self.data.get("sourceLanguage") or "en"

    @property
    def version(self) -> str:
        return str(self.data.get("version", ""))

    @property
    def writable(self) -> bool:
        return self.roundtrip_ok and not self.duplicates

    @property
    def not_writable_reason(self) -> str:
        if self.duplicates:
            names = ", ".join(repr(k) for k in self.duplicates[:3])
            return (f"the file repeats the key {names}; the later copy silently wins, so delete the one "
                    "you do not want in Xcode first")
        if not self.roundtrip_ok:
            return self.roundtrip_note
        return ""

    def locales(self) -> list[str]:
        found: set[str] = set()
        for entry in self.strings.values():
            found.update((entry.get("localizations") or {}).keys())
        return sorted(found)


def _first_difference(expected: str, actual: str) -> str:
    for index, (a, b) in enumerate(zip(expected, actual)):
        if a != b:
            line = expected.count("\n", 0, index) + 1
            lo = max(0, index - 30)
            return (f"line {line}: the file has {expected[lo:index + 30]!r} where this tool would write "
                    f"{actual[lo:index + 30]!r}")
    return f"the file is {len(expected)} characters and re-emitting gives {len(actual)}"


def load(path) -> Catalog:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CatalogError(f"{path} is not valid UTF-8 ({exc})") from exc

    duplicates: list[str] = []

    def hook(pairs):
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen and key not in duplicates:
                duplicates.append(key)
            seen.add(key)
        return dict(pairs)

    try:
        data = json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("strings"), dict):
        raise CatalogError(f"{path} is not a String Catalog (there is no top-level 'strings' object)")

    base = Style(escape_slash="\\/" in text, trailing_newline=text.endswith("\n"))
    candidates = [(eo, ea) for eo in ("blank", "compact") for ea in ("blank", "compact")]
    for empty_object, empty_array in candidates:
        style = Style(empty_object, empty_array, base.escape_slash, base.trailing_newline)
        if dumps(data, style) == text:
            return Catalog(path, data, text, style, True, duplicates=duplicates)
    note = "emitting the parsed file does not reproduce it byte for byte: " + _first_difference(text, dumps(data, base))
    return Catalog(path, data, text, base, False, note, duplicates)


def compile_check(path) -> tuple[bool | None, str]:
    """Ask Xcode's own compiler (xcstringstool) to read the catalog.

    Returns (True, ""), (False, message) or (None, reason) when the tool is not
    available. It checks that Xcode can read the file; it is lenient about
    meaning (an unknown state or a placeholder that does not match compile
    fine), which is what the audit is for.
    """
    exe = shutil.which("xcrun")
    if exe is None:
        return None, "xcrun is not available (not a Mac with Xcode)"
    with tempfile.TemporaryDirectory() as out:
        try:
            result = subprocess.run([exe, "xcstringstool", "compile", "--output-directory", out, str(path)],
                                    capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"could not run xcstringstool: {exc}"
    if result.returncode == 0:
        return True, ""
    message = (result.stderr or result.stdout).strip()
    if "unable to find utility" in message or "xcstringstool" in message and "not a developer tool" in message:
        return None, message
    return False, message


def compile_check_text(text: str, name: str) -> tuple[bool | None, str]:
    with tempfile.TemporaryDirectory() as folder:
        candidate = Path(folder) / name
        candidate.write_text(text, encoding="utf-8")
        return compile_check(candidate)


def save(catalog: Catalog, data: dict, verify: bool = True) -> str:
    """Write `data` in the catalog's own style. Returns the text written.

    Refuses unless the original round-trips, because only then is every changed
    line a change that was asked for. Unless `verify` is off, the result is
    also handed to Xcode's compiler first, so a file Xcode cannot read is never
    put in place.
    """
    if not catalog.writable:
        raise CatalogError(f"not writing {catalog.path}: {catalog.not_writable_reason}")
    text = dumps(data, catalog.style)
    if json.loads(text) != data:
        raise CatalogError("internal check failed: the text to write does not parse back to the same data")
    if verify:
        ok, message = compile_check_text(text, catalog.path.name)
        if ok is False:
            raise CatalogError(f"Xcode's xcstringstool rejects the result, so nothing was written: {message}")
    tmp = catalog.path.with_name(catalog.path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        shutil.copymode(catalog.path, tmp)
        os.replace(tmp, catalog.path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return text


# -- units of text inside a localization -------------------------------------------------------

Path_ = tuple  # ("plural", "one") or ("device", "iphone", "plural", "other") or ("substitution", "n", ...)


def iter_units(node: dict, path: tuple = ()):
    """Yield (path, stringUnit) for every piece of text in a localization."""
    if not isinstance(node, dict):
        return
    unit = node.get("stringUnit")
    if isinstance(unit, dict):
        yield path, unit
    for kind, branches in (node.get("variations") or {}).items():
        if isinstance(branches, dict):
            for name, sub in branches.items():
                yield from iter_units(sub, path + (kind, name))
    for name, sub in (node.get("substitutions") or {}).items():
        yield from iter_units(sub, path + ("substitution", name))


def plain_value(node: dict | None) -> str | None:
    """The text of a localization that is a single plain string, else None."""
    if not isinstance(node, dict):
        return None
    unit = node.get("stringUnit")
    if isinstance(unit, dict) and "variations" not in node and "substitutions" not in node:
        value = unit.get("value")
        return value if isinstance(value, str) else None
    return None


def plural_forms(node: dict | None) -> dict | None:
    """{category: text} when a localization is exactly one top-level plural variation."""
    if not isinstance(node, dict) or "substitutions" in node or "stringUnit" in node:
        return None
    variations = node.get("variations")
    if not isinstance(variations, dict) or set(variations) != {"plural"}:
        return None
    forms = {}
    for category, sub in variations["plural"].items():
        value = plain_value(sub)
        if value is None:
            return None
        forms[category] = value
    return forms


def unsupported_reason(node: dict | None) -> str | None:
    """Why this tool will not write a localization, or None if it can."""
    if not isinstance(node, dict):
        return None
    if "substitutions" in node:
        return "has substitutions"
    variations = node.get("variations")
    if isinstance(variations, dict):
        if "device" in variations:
            return "has device variations"
        if set(variations) - {"plural"}:
            return "has unsupported variations"
        if plural_forms(node) is None:
            return "has nested plural variations"
    return None


# -- format specifiers -------------------------------------------------------------------------

_SUBSTITUTION = re.compile(r"%#@([A-Za-z_][A-Za-z0-9_]*)@")
_SPEC = re.compile(
    r"%(?:(?P<pos>\d+)\$)?(?P<flags>[-+ 0#']*)(?P<width>\d+|\*)?(?:\.(?P<prec>\d+|\*))?"
    r"(?P<len>hh|h|ll|l|q|L|z|t|j)?(?P<conv>[@dDiuUxXoOfFeEgGaAcCsSp])"
)


@dataclass(frozen=True)
class Spec:
    raw: str
    position: int | None
    kind: str


def _kind(conv: str, length: str | None) -> str:
    wide = length in ("l", "ll", "q", "z", "t", "j")
    if conv == "@":
        return "object"
    if conv in "di":
        return "int8" if length == "hh" else "int16" if length == "h" else "int64" if wide else "int32"
    if conv == "D":
        return "int64"
    if conv in "uxXo":
        return "uint8" if length == "hh" else "uint16" if length == "h" else "uint64" if wide else "uint32"
    if conv in "UO":
        return "uint64"
    if conv in "fFeEgGaA":
        return "longdouble" if length == "L" else "double"
    return {"c": "char", "C": "unichar", "s": "cstring", "S": "wcstring", "p": "pointer"}[conv]


def _is_lookalike(m: re.Match) -> bool:
    """`% d` in "50% discount", `% ho` in "50% hombres": a percent sign, a space and letters that
    happen to read as a conversion.

    It is technically a specifier (the space flag), but nobody writes that on
    purpose; it is the literal percent sign of a sentence. It is kept out of
    the comparison and reported on its own.
    """
    return m.group("flags") == " " and not m.group("pos") and not m.group("width") and not m.group("prec")


def parse_specs(text: str) -> list[Spec]:
    cleaned = text.replace("%%", "")
    cleaned = _SUBSTITUTION.sub("", cleaned)
    specs = []
    for m in _SPEC.finditer(cleaned):
        if _is_lookalike(m):
            continue
        specs.append(Spec(m.group(0), int(m.group("pos")) if m.group("pos") else None,
                          _kind(m.group("conv"), m.group("len"))))
    return specs


def percent_lookalikes(text: str) -> list[str]:
    """Literal percent signs that a format string would read as a placeholder (`% d`)."""
    cleaned = _SUBSTITUTION.sub("", text.replace("%%", ""))
    return [m.group(0) for m in _SPEC.finditer(cleaned) if _is_lookalike(m)]


def substitution_refs(text: str) -> list[str]:
    return _SUBSTITUTION.findall(text)


def _by_position(specs: list[Spec]) -> dict[int, Spec]:
    out: dict[int, Spec] = {}
    nxt = 1
    for spec in specs:
        if spec.position is not None:
            position = spec.position
        else:
            position, nxt = nxt, nxt + 1
        out.setdefault(position, spec)
    return out


def compare_specs(source: str, target: str, allow_missing: bool = False) -> list[str]:
    """Problems with the format specifiers of `target` against `source`.

    Arguments are matched by position (an explicit `%2$@`, else the order of
    appearance), so a translation may reorder them only with positional
    specifiers. `allow_missing` is for plural forms such as "One item", which
    legitimately leave the number out.
    """
    problems: list[str] = []
    src, tgt = _by_position(parse_specs(source)), _by_position(parse_specs(target))
    reorder = False
    for position, spec in src.items():
        found = tgt.get(position)
        if found is None:
            if not allow_missing:
                problems.append(f"missing {spec.raw} (argument {position})")
        elif found.kind != spec.kind:
            problems.append(f"argument {position} is {found.raw} but the source has {spec.raw}")
            reorder = True
    for position, spec in tgt.items():
        if position not in src:
            problems.append(f"extra {spec.raw} (argument {position}) that the source does not have")
    if reorder and sorted(s.kind for s in src.values()) == sorted(s.kind for s in tgt.values()):
        problems.append("the same placeholders in a different order: reorder with positional specifiers "
                        "such as %2$@ and %1$lld")
    src_refs, tgt_refs = set(substitution_refs(source)), set(substitution_refs(target))
    for name in sorted(src_refs - tgt_refs):
        problems.append(f"missing substitution %#@{name}@")
    for name in sorted(tgt_refs - src_refs):
        problems.append(f"extra substitution %#@{name}@")
    return problems


# -- plural categories -------------------------------------------------------------------------

# CLDR plural categories that change the text for whole numbers, by language. The ones not
# listed need "other" only. Romance languages also have "many" for very large and compact
# numbers; it is left out because whole-number counts never select it.
_PLURALS = {
    **dict.fromkeys(["en", "de", "es", "fr", "it", "pt", "nl", "ca", "gl", "sv", "da", "nb", "no", "fi",
                     "tr", "el", "hu", "bg", "et", "eu", "sq"], ("one", "other")),
    **dict.fromkeys(["ja", "ko", "zh", "vi", "th", "id", "ms", "my", "lo", "km"], ("other",)),
    **dict.fromkeys(["ru", "uk", "pl", "be"], ("one", "few", "many", "other")),
    **dict.fromkeys(["cs", "sk"], ("one", "few", "other")),
    "ar": ("zero", "one", "two", "few", "many", "other"),
    "he": ("one", "two", "other"),
    "hr": ("one", "few", "other"),
    "sr": ("one", "few", "other"),
    "sl": ("one", "two", "few", "other"),
    "lt": ("one", "few", "other"),
    "lv": ("zero", "one", "other"),
    "ro": ("one", "few", "other"),
}


_LATIN = {"en", "de", "es", "fr", "it", "pt", "nl", "ca", "gl", "eu", "sv", "da", "nb", "no", "fi", "tr", "pl",
          "cs", "sk", "ro", "hu", "hr", "sl", "lt", "lv", "et", "vi", "id", "ms"}


def uses_latin_script(locale: str) -> bool:
    return base_language(locale) in _LATIN


def base_language(locale: str) -> str:
    return re.split(r"[-_]", locale, maxsplit=1)[0].lower()


def plural_categories(locale: str) -> tuple[str, ...]:
    return _PLURALS.get(base_language(locale), ("other",))


# -- text helpers ------------------------------------------------------------------------------

def has_letters(text: str) -> bool:
    """True when `text` has anything to translate once format specifiers are removed."""
    stripped = _SUBSTITUTION.sub("", text.replace("%%", ""))
    stripped = _SPEC.sub(lambda m: m.group(0) if _is_lookalike(m) else "", stripped)
    return any(ch.isalpha() for ch in stripped)


def swift_literal_body(key: str) -> str:
    """The key as it would be written between the quotes of a Swift string literal."""
    return (key.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            .replace("\r", "\\r").replace("\t", "\\t"))
