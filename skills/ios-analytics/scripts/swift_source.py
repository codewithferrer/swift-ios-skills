"""A small Swift reader: enough structure to find types, functions, calls and closures.

It is not a parser. It blanks comments and string contents (so a brace or a
function name inside either cannot fool the matching), pairs brackets, and
indexes `func`, `init`, `body` and type declarations with their ranges. That is
what the analytics scan needs and nothing more, and it works on a project that
does not build.
"""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_DIRS = {".git", ".build", "DerivedData", "Pods", "Carthage", "node_modules", ".claude", "build",
                 ".swiftpm", "xcuserdata", "checkouts", "SourcePackages"}
KEYWORDS = {"if", "guard", "while", "switch", "for", "return", "catch", "case", "in", "func", "init", "self",
            "super", "where", "throw", "try", "await", "repeat", "else", "do", "let", "var", "as", "is", "some",
            "any", "nil", "true", "false", "defer", "async", "throws", "rethrows", "inout", "static", "class",
            "struct", "enum", "protocol", "extension", "import", "typealias", "default", "break", "continue",
            "fallthrough", "get", "set", "willSet", "didSet", "Self"}


@dataclass
class StringLiteral:
    start: int
    end: int
    value: str
    interpolated: bool


@dataclass
class Declaration:
    kind: str            # "type" | "func"
    name: str
    start: int           # offset of the keyword
    body: tuple          # (open_brace, close_brace) offsets, or None
    owner: str | None = None       # enclosing type name, for functions
    inherits: str = ""             # text after the type name up to the brace
    params: str = ""               # parameter list text, for functions
    keyword: str = ""              # class | struct | enum | actor | extension | protocol


@dataclass
class SwiftFile:
    path: Path
    text: str
    code: str
    strings: dict
    pairs: dict
    line_starts: list
    types: list = field(default_factory=list)
    funcs: list = field(default_factory=list)
    rpairs: dict = field(default_factory=dict)

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset)

    def literal_at(self, offset: int) -> StringLiteral | None:
        return self.strings.get(offset)

    def type_at(self, offset: int) -> Declaration | None:
        best = None
        for decl in self.types:
            if decl.body and decl.body[0] < offset < decl.body[1]:
                if best is None or decl.body[0] > best.body[0]:
                    best = decl
        return best

    def func_at(self, offset: int) -> Declaration | None:
        best = None
        for decl in self.funcs:
            if decl.body and decl.body[0] < offset < decl.body[1]:
                if best is None or decl.body[0] > best.body[0]:
                    best = decl
        return best


# -- lexing --------------------------------------------------------------------------------------

def _skip_string(text: str, i: int):
    """Scan a string literal that starts at text[i]; return (end, interpolated) or None."""
    n = len(text)
    j = i
    hashes = 0
    while j < n and text[j] == "#":
        hashes += 1
        j += 1
    if j >= n or text[j] != '"':
        return None
    triple = text.startswith('"""', j)
    quote = '"""' if triple else '"'
    close = quote + "#" * hashes
    k = j + len(quote)
    interpolated = False
    while k < n:
        if text.startswith(close, k):
            return k + len(close), interpolated
        if text[k] == "\\" and text.startswith("\\" + "#" * hashes, k):
            m = k + 1 + hashes
            if m < n and text[m] == "(":
                interpolated = True
                depth, k = 1, m + 1
                while k < n and depth:
                    ch = text[k]
                    if ch == '"' or (ch == "#" and text.startswith('#"', k)):
                        skipped = _skip_string(text, k)
                        if skipped:
                            k = skipped[0]
                            continue
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    k += 1
                continue
            k = m + 1
            continue
        if not triple and text[k] == "\n":
            return k, interpolated    # unterminated; do not run away
        k += 1
    return n, interpolated


def _blank(buf: list, a: int, b: int) -> None:
    for i in range(a, b):
        if buf[i] != "\n":
            buf[i] = " "


def lex(text: str):
    n = len(text)
    buf = list(text)
    strings: dict = {}
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            _blank(buf, i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            _blank(buf, i, j)
            i = j
            continue
        if c == '"' or (c == "#" and text.startswith("#", i) and re.match(r"#+\"", text[i:i + 8])):
            hashes = 0
            while text[i + hashes] == "#":
                hashes += 1
            skipped = _skip_string(text, i)
            if skipped:
                end, interpolated = skipped
                q = 3 if text.startswith('"""', i + hashes) else 1
                inner_a = i + hashes + q
                inner_b = max(inner_a, end - q - hashes)
                strings[i] = StringLiteral(i, end, text[inner_a:inner_b], interpolated)
                _blank(buf, inner_a, inner_b)
                i = end
                continue
        i += 1
    return "".join(buf), strings


def _pair_brackets(code: str) -> dict:
    pairs: dict = {}
    stack: list = []
    close_for = {"(": ")", "[": "]", "{": "}"}
    for i, ch in enumerate(code):
        if ch in close_for:
            stack.append((ch, i))
        elif ch in ")]}":
            while stack:
                opener, pos = stack.pop()
                if close_for[opener] == ch:
                    pairs[pos] = i
                    break
    return pairs


_TYPE_RE = re.compile(r"\b(class|struct|enum|actor|extension|protocol)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
_FUNC_RE = re.compile(r"\bfunc\s+([A-Za-z_]\w*|`[^`]+`)\s*(?:<[^>(]*>)?\s*\(")
_INIT_RE = re.compile(r"\binit\s*[?!]?\s*\(")
_BODY_RE = re.compile(r"\bvar\s+body\s*:\s*some\s+\w+\s*\{")
_OBSERVER_RE = re.compile(r"\b(didSet|willSet)\s*(?:\([^)]*\))?\s*\{")
_DECL_START = re.compile(r"\s*(?:func|var|let|init|subscript|case|typealias|@|\})")


def _body_after(code: str, pairs: dict, start: int):
    """The brace pair that opens the body following `start`, or None for a bodiless declaration."""
    j = start
    n = len(code)
    while j < n:
        ch = code[j]
        if ch == "{":
            return j, pairs.get(j)
        if ch == "(" or ch == "[":
            j = pairs.get(j, j) + 1
            continue
        if ch == "\n":
            m = _DECL_START.match(code, j + 1)
            if m and code[j + 1:j + 400].lstrip().split(None, 1)[:1] not in ([],):
                head = code[j + 1:].lstrip()
                if re.match(r"(?:func|var|let|init|subscript|case|typealias|\}|@|private|public|internal|fileprivate|static|final|override|open)\b", head) or head.startswith("}"):
                    return None
        j += 1
    return None


def parse(path: Path, text: str | None = None) -> SwiftFile:
    if text is None:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    code, strings = lex(text)
    pairs = _pair_brackets(code)
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    sf = SwiftFile(Path(path), text, code, strings, pairs, starts)
    sf.rpairs = {close: opener for opener, close in pairs.items()}

    for m in _TYPE_RE.finditer(code):
        kind, name = m.group(1), m.group(2)
        if name in KEYWORDS or code[max(0, m.start() - 1):m.start()] == ".":
            continue
        body = _body_after(code, pairs, m.end())
        if body and body[1] is not None:
            header = code[m.end():body[0]].strip()
            sf.types.append(Declaration("type", name.split(".")[-1], m.start(), body, inherits=header,
                                        keyword=kind))

    for m in _FUNC_RE.finditer(code):
        open_paren = m.end() - 1
        close_paren = pairs.get(open_paren)
        if close_paren is None:
            continue
        body = _body_after(code, pairs, close_paren + 1)
        decl = Declaration("func", m.group(1).strip("`"), m.start(),
                           body if body and body[1] is not None else None,
                           params=code[open_paren + 1:close_paren])
        sf.funcs.append(decl)
    for m in _INIT_RE.finditer(code):
        open_paren = m.end() - 1
        close_paren = pairs.get(open_paren)
        if close_paren is None:
            continue
        body = _body_after(code, pairs, close_paren + 1)
        sf.funcs.append(Declaration("func", "init", m.start(),
                                    body if body and body[1] is not None else None,
                                    params=code[open_paren + 1:close_paren]))
    for m in _BODY_RE.finditer(code):
        open_brace = m.end() - 1
        sf.funcs.append(Declaration("func", "body", m.start(), (open_brace, pairs.get(open_brace))))
    for m in _OBSERVER_RE.finditer(code):
        open_brace = m.end() - 1
        close = pairs.get(open_brace)
        if close is None:
            continue
        # the property whose observer this is: the nearest `var name` before the enclosing brace
        enclosing = None
        for o, c in pairs.items():
            if code[o] == "{" and o < m.start() < c and (enclosing is None or o > enclosing):
                enclosing = o
        prop = None
        if enclosing is not None:
            head = code[max(0, enclosing - 240):enclosing]
            found = re.findall(r"\bvar\s+([A-Za-z_]\w*)", head)
            prop = found[-1] if found else None
        sf.funcs.append(Declaration("func", f"{prop}.{m.group(1)}" if prop else m.group(1), m.start(),
                                    (open_brace, close)))

    for decl in sf.funcs:
        owner = sf.type_at(decl.start)
        decl.owner = owner.name if owner else None
    return sf


def iter_swift_files(root: Path):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(files):
            if name.endswith(".swift"):
                yield Path(folder) / name


# -- helpers over a file -----------------------------------------------------------------------

_NAME_CALL = re.compile(r"([A-Za-z_]\w*)\s*[?!]?\s*\(")
_DECL_WORDS = {"func", "case", "enum", "struct", "class", "protocol", "extension", "typealias", "actor", "macro"}


@dataclass
class Call:
    name: str
    receiver: str       # "AnalyticsManager.shared", "self", "viewModel", "UNUserNotificationCenter.current()"
    open_paren: int
    start: int


def _receiver(sf: SwiftFile, start: int) -> str:
    """The dotted chain before the name at `start`, with `()` or `[]` kept where a call or subscript sits."""
    code = sf.code
    parts: list = []
    i = start
    while True:
        j = i - 1
        while j >= 0 and code[j] in " \t\n":
            j -= 1
        if j >= 1 and code[j] == "." and code[j - 1] != ".":
            j -= 1
            while j >= 0 and code[j] in " \t\n":
                j -= 1
            if j >= 0 and code[j] in "?!":
                j -= 1
            if j >= 0 and code[j] in ")]":
                opener = sf.rpairs.get(j)
                if opener is None:
                    break
                k = opener - 1
                while k >= 0 and code[k] in " \t":
                    k -= 1
                end = k + 1
                while k >= 0 and (code[k].isalnum() or code[k] == "_"):
                    k -= 1
                ident = code[k + 1:end]
                if not ident:
                    break
                parts.append(ident + ("()" if code[j] == ")" else "[]"))
                i = k + 1
                continue
            end = j + 1
            while j >= 0 and (code[j].isalnum() or code[j] == "_"):
                j -= 1
            ident = code[j + 1:end]
            if not ident:
                break
            parts.append(ident)
            i = j + 1
            continue
        break
    return ".".join(reversed(parts))


def calls_in(sf: SwiftFile, a: int, b: int) -> list:
    out = []
    code = sf.code
    for m in _NAME_CALL.finditer(code, a, b):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        start = m.start(1)
        if start > 0 and (code[start - 1].isalnum() or code[start - 1] in "_$"):
            continue
        prev = re.search(r"([A-Za-z_]\w*)\s*$", code[max(0, start - 24):start])
        if prev and prev.group(1) in _DECL_WORDS:
            continue
        out.append(Call(name, _receiver(sf, start), m.end() - 1, start))
    return out


def split_args(sf: SwiftFile, open_paren: int) -> list:
    """Top-level arguments of the call whose '(' is at open_paren: [(label|None, start, end)]."""
    close = sf.pairs.get(open_paren)
    if close is None:
        return []
    args, depth, start = [], 0, open_paren + 1
    i = start
    code = sf.code
    while i < close:
        ch = code[i]
        if ch in "([{":
            i = sf.pairs.get(i, i) + 1
            continue
        if ch == ",":
            args.append((start, i))
            start = i + 1
        i += 1
    if code[start:close].strip():
        args.append((start, close))
    result = []
    for a, b in args:
        chunk = code[a:b]
        m = re.match(r"\s*([A-Za-z_]\w*)\s*:(?!:)", chunk)
        if m:
            result.append((m.group(1), a + m.end(), b))
        else:
            result.append((None, a + (len(chunk) - len(chunk.lstrip())), b))
    return result


def first_string(sf: SwiftFile, a: int, b: int) -> StringLiteral | None:
    """The string literal that makes up the expression text[a:b], if it is just a literal."""
    chunk = sf.code[a:b]
    stripped = chunk.strip()
    if not stripped.startswith(('"', "#")):
        return None
    offset = a + (len(chunk) - len(chunk.lstrip()))
    lit = sf.strings.get(offset)
    if lit and sf.code[lit.end:b].strip() == "":
        return lit
    return None


def trailing_closure(sf: SwiftFile, close_paren: int, limit: int):
    """The `{ ... }` that follows a call's ')' directly (a trailing closure), as (open, close)."""
    j = close_paren + 1
    code = sf.code
    while j < limit and code[j] in " \t\n":
        j += 1
    if j < limit and code[j] == "{":
        end = sf.pairs.get(j)
        if end is not None:
            return j, end
    return None
