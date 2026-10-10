"""ASO analysis rules for an iOS listing. Pure logic: no network, no
third-party imports, so every rule can be unit tested and used offline.

The input is a metadata folder in the layout fastlane's `deliver` uses (and
that the app-store-release skill's pull_metadata.py writes):

    metadata/
      en-US/  name.txt  subtitle.txt  keywords.txt  promotional_text.txt  description.txt
      es-ES/  ...

Nothing here knows search volumes or rankings; those need data this code
cannot see. What it checks is the part that is deterministic and where
hand-counting goes wrong: character budgets, duplicated words, and which
localizations feed which storefront.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

NAME_LIMIT = 30
SUBTITLE_LIMIT = 30
KEYWORDS_LIMIT = 100
PROMOTIONAL_LIMIT = 170
DESCRIPTION_LIMIT = 4000

# Words that spend keyword budget on nothing: Apple's product page guidance
# says to leave out the word "app" and the category name.
GENERIC_KEYWORDS = {"app", "apps", "free", "gratis", "best", "mejor"}
# Unverifiable claims and pricing that Apple's guidance says to keep out of
# the name and subtitle.
CLAIM_RE = re.compile(r"(?i)(?<!\w)(best|#1|no\.? ?1|number one|free|top|official|mejor|gratis|oficial|n[ºo°]\.? ?1)(?!\w)")

CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
LOCALE_DIR_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,4})*$")
FIELDS = ("name", "subtitle", "keywords", "promotional_text", "description")

SEVERITY_ORDER = {"fix": 0, "opportunity": 1, "check": 2}


# -- Text helpers --------------------------------------------------------------


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def words(text: str) -> list[str]:
    """Lowercased words. CJK text has no spaces, so a CJK run is one token."""
    return [w.lower() for w in WORD_RE.findall(text or "")]


def meaningful(token: str) -> bool:
    """Single letters are noise; two-letter keywords such as qr, tv or ai are
    real, and a lone CJK character can be a whole word.
    """
    return has_cjk(token) or len(token) >= 2


def keyword_terms(keywords: str) -> list[str]:
    return [t.strip() for t in (keywords or "").split(",") if t.strip()]


def keyword_tokens(keywords: str) -> list[str]:
    """What Apple indexes from the field: every word of every term; a term
    with CJK characters is kept whole because it cannot be split on spaces.
    """
    tokens: list[str] = []
    for term in keyword_terms(keywords):
        tokens.extend([term.lower()] if has_cjk(term) else words(term))
    return tokens


def plural_variant(word: str, others: set) -> str | None:
    """A singular/plural counterpart of `word` found in `others` (naive
    suffix rules that suit English and the Romance languages).
    """
    if has_cjk(word) or len(word) < 3:
        return None
    candidates = {word + "s", word + "es"}
    if word.endswith("es") and len(word) > 4:
        candidates.add(word[:-2])
    if word.endswith("s") and len(word) > 3:
        candidates.add(word[:-1])
    return next((c for c in sorted(candidates) if c in others), None)


# -- Loading --------------------------------------------------------------------


def load_listings(directory: Path) -> dict[str, dict[str, str]]:
    """locale -> {field: text}, for locale folders holding any known field."""
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a directory")
    listings: dict[str, dict[str, str]] = {}
    for locale_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        if not LOCALE_DIR_RE.match(locale_dir.name):
            continue
        fields = {}
        for name in FIELDS:
            path = locale_dir / f"{name}.txt"
            if path.is_file():
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
                if text:
                    fields[name] = text
        if fields:
            listings[locale_dir.name] = fields
    return listings


def load_storefronts(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["storefronts"]


# -- Findings ---------------------------------------------------------------------


@dataclass
class Finding:
    severity: str  # fix | opportunity | check
    where: str     # a locale, a storefront code, or "*"
    message: str


def _kw_chars(text: str) -> int:
    return len(text)


def audit_locale(locale: str, fields: dict, category: str | None = None) -> list[Finding]:
    out: list[Finding] = []
    add = lambda sev, msg: out.append(Finding(sev, locale, msg))  # noqa: E731

    name = fields.get("name", "")
    subtitle = fields.get("subtitle", "")
    keywords = fields.get("keywords", "")
    promo = fields.get("promotional_text", "")
    description = fields.get("description", "")

    if not name:
        add("check", "no name.txt in the folder, so the keywords and subtitle cannot be compared with the "
                     "name (pull the metadata first, or add the file)")
    if len(name) > NAME_LIMIT:
        add("fix", f"name is {len(name)} characters, the limit is {NAME_LIMIT}")
    if len(subtitle) > SUBTITLE_LIMIT:
        add("fix", f"subtitle is {len(subtitle)} characters, the limit is {SUBTITLE_LIMIT}")
    if len(promo) > PROMOTIONAL_LIMIT:
        add("fix", f"promotional text is {len(promo)} characters, the limit is {PROMOTIONAL_LIMIT}")
    if len(description) > DESCRIPTION_LIMIT:
        add("fix", f"description is {len(description)} characters, the limit is {DESCRIPTION_LIMIT}")

    # Subtitle: indexed text that is often left empty or spent repeating the name.
    if not subtitle:
        add("opportunity", f"no subtitle: {SUBTITLE_LIMIT} characters of indexed text are unused")
    else:
        repeated = sorted(set(words(subtitle)) & set(words(name)))
        if repeated:
            add("opportunity", f"subtitle repeats words already in the name ({', '.join(repeated)}): "
                               "Apple indexes each word once, so that part of the subtitle adds no keywords")
    for label, text in (("name", name), ("subtitle", subtitle)):
        claims = sorted({m.group(1).lower() for m in CLAIM_RE.finditer(text)})
        if claims:
            add("check", f"{label} contains {', '.join(claims)}: Apple's guidance keeps unverifiable claims "
                         "and pricing out of the name and subtitle")

    # Keywords.
    if not keywords:
        add("opportunity", f"no keywords: {KEYWORDS_LIMIT} characters of indexed text are unused")
    else:
        used = _kw_chars(keywords)
        if used > KEYWORDS_LIMIT:
            add("fix", f"keywords are {used} characters, the limit is {KEYWORDS_LIMIT}")
        elif KEYWORDS_LIMIT - used >= 15:
            add("opportunity", f"keywords use {used} of {KEYWORDS_LIMIT} characters: "
                               f"{KEYWORDS_LIMIT - used} free")
        encoded = len(keywords.encode("utf-8"))
        if used <= KEYWORDS_LIMIT < encoded:
            add("check", f"keywords are {used} characters but {encoded} UTF-8 bytes. App Store Connect enforces "
                         "characters (it accepts this), but Apple's Help also says bytes, so whether the tail is "
                         "indexed is unverified")

        wasted = len(re.findall(r",\s+|\s+,", keywords))
        if wasted:
            add("fix", f"spaces around commas: {wasted} spot(s) spend characters for nothing; "
                       "write the field as a,b,c")
        if re.search(r",\s*,|^\s*,|,\s*$", keywords):
            add("fix", "empty term (a stray or doubled comma)")

        tokens = keyword_tokens(keywords)
        seen: set = set()
        dupes = []
        for tok in tokens:
            if tok in seen and tok not in dupes:
                dupes.append(tok)
            seen.add(tok)
        if dupes:
            add("fix", f"repeated within the field: {', '.join(dupes)}")
        plurals = []
        token_set = set(tokens)
        for tok in sorted(token_set):
            variant = plural_variant(tok, token_set)
            if variant and {tok, variant} not in [set(p) for p in plurals]:
                plurals.append((tok, variant))
        if plurals:
            shown = ", ".join(f"{a}/{b}" for a, b in plurals)
            add("check", f"possible singular and plural of the same word: {shown}. Apple's guidance says to "
                         "leave out plurals of a singular already included")

        indexed_elsewhere = set(words(name)) | set(words(subtitle))
        redundant = sorted({t for t in tokens if t in indexed_elsewhere
                            or (has_cjk(t) and (t in name or t in subtitle))})
        if redundant:
            add("fix", f"already indexed through the name or subtitle: {', '.join(redundant)}")
        generic = sorted({t for t in tokens if t in GENERIC_KEYWORDS})
        if generic:
            add("check", f"generic word(s) {', '.join(generic)}: Apple's guidance says to leave out "
                         "\"app\" and similar")
        if category:
            cat_words = set(words(category))
            hit = sorted({t for t in tokens if t in cat_words})
            if hit:
                add("check", f"category word(s) {', '.join(hit)}: the primary category is already used for relevance")

    if not promo:
        add("opportunity", f"no promotional text: {PROMOTIONAL_LIMIT} characters you can change without a new "
                           "version (not a ranking factor, a conversion lever)")
    return out


def audit_storefront(code: str, info: dict, listings: dict) -> list[Finding]:
    out: list[Finding] = []
    indexed = [info["default"], *info["additional"]]
    present = [loc for loc in indexed if loc in listings]
    if info["default"] not in listings:
        out.append(Finding("opportunity", code,
                           f"{info['name']}: the default locale {info['default']} has no localization"))

    # Words that more than one of the locales feeding this storefront spend.
    # A word in two names is the brand and repeats on purpose, so only words
    # that sit in at least one keyword field count.
    locales_of: dict[str, set] = {}
    in_keywords: set = set()
    for loc in present:
        f = listings[loc]
        kw = set(keyword_tokens(f.get("keywords", "")))
        for word in kw | set(words(f.get("subtitle", ""))) | set(words(f.get("name", ""))):
            if meaningful(word):
                locales_of.setdefault(word, set()).add(loc)
        in_keywords |= {w for w in kw if meaningful(w)}
    repeated = {w: locs for w, locs in locales_of.items() if len(locs) >= 2 and w in in_keywords}
    if repeated:
        shown = "; ".join(f"{w} ({', '.join(sorted(locs))})" for w, locs in sorted(repeated.items()))
        out.append(Finding("check", code,
                           f"{info['name']}: words spent in more than one localization that feeds this "
                           f"storefront: {shown}. A word counts once per storefront, so every extra "
                           "occurrence is wasted"))

    kw_locales = [loc for loc in present if listings[loc].get("keywords")]
    for i, a in enumerate(kw_locales):
        for b in kw_locales[i + 1:]:
            if listings[a]["keywords"].lower() == listings[b]["keywords"].lower():
                out.append(Finding("fix", code,
                                   f"{info['name']}: {a} and {b} have identical keyword fields, so one of "
                                   "them adds nothing for this storefront"))
    return out


def missing_additional_locales(listings: dict, table: dict, codes: list[str]) -> list[Finding]:
    """One finding per localization that several audited storefronts list as
    additional and the app lacks, instead of repeating it per storefront.
    """
    wanted: dict[str, list[str]] = {}
    for code in codes:
        info = table.get(code)
        if not info or info["default"] not in listings:
            continue
        for loc in info["additional"]:
            if loc not in listings:
                wanted.setdefault(loc, []).append(code)
    out = []
    for loc, stores in sorted(wanted.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out.append(Finding(
            "opportunity", ",".join(stores),
            f"{loc} is an additional language for {len(stores)} storefront(s) you audit and you have no {loc} "
            f"localization. Adding it gives each of them another {KEYWORDS_LIMIT} characters of keywords; "
            "worth doing only where you can write text that makes sense in that language"))
    return out


def merge_identical(findings: list[Finding]) -> list[Finding]:
    """Locale findings with the same message (for example "no promotional
    text") become one finding that names every locale.
    """
    groups: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    for f in findings:
        key = (f.severity, f.message)
        if key not in groups:
            order.append(key)
        groups.setdefault(key, []).append(f.where)
    merged = []
    for severity, message in order:
        places = groups[(severity, message)]
        merged.append(Finding(severity, ", ".join(places) if len(places) > 1 else places[0], message))
    return merged


def default_storefronts(listings: dict, table: dict) -> list[str]:
    """Storefronts whose default locale the app has a localization for."""
    return [code for code, info in table.items() if info["default"] in listings]


def audit(listings: dict, table: dict, storefronts: list[str] | None = None, category: str | None = None) -> list[Finding]:
    locale_findings: list[Finding] = []
    for locale in sorted(listings):
        locale_findings.extend(audit_locale(locale, listings[locale], category))
    codes = storefronts if storefronts is not None else default_storefronts(listings, table)

    findings = merge_identical(locale_findings)
    for code in codes:
        if code in table:
            findings.extend(audit_storefront(code, table[code], listings))
    findings.extend(missing_additional_locales(listings, table, codes))
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.where))
    return findings


# -- Rendering ----------------------------------------------------------------------


def render_fields(listings: dict) -> str:
    header = f"{'locale':<9}{'name':>5}{'sub':>5}{'keywords':>10}{'bytes':>7}{'promo':>7}{'desc':>6}"
    lines = [header, "-" * len(header)]
    for locale in sorted(listings):
        f = listings[locale]
        kw = f.get("keywords", "")
        lines.append(
            f"{locale:<9}{len(f.get('name', '')):>5}{len(f.get('subtitle', '')):>5}"
            f"{len(kw):>7}/100{len(kw.encode('utf-8')):>7}{len(f.get('promotional_text', '')):>7}"
            f"{len(f.get('description', '')):>6}"
        )
    return "\n".join(lines)


def render_findings(findings: list[Finding]) -> str:
    if not findings:
        return "No findings."
    labels = {"fix": "FIX", "opportunity": "OPPORTUNITY", "check": "CHECK"}
    out = []
    for severity in ("fix", "opportunity", "check"):
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        out.append(f"{labels[severity]} ({len(group)})")
        for f in group:
            out.append(f"  [{f.where}] {f.message}")
        out.append("")
    return "\n".join(out).rstrip()


# -- Keyword packing ---------------------------------------------------------------------


@dataclass
class PackResult:
    field: str
    chosen: list = field(default_factory=list)
    skipped: list = field(default_factory=list)    # (word, reason)
    did_not_fit: list = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.field)


def pack_keywords(
    candidates: list[str],
    already_indexed: set | None = None,
    limit: int = KEYWORDS_LIMIT,
    split_phrases: bool = True,
) -> PackResult:
    """Fill a keyword field greedily, in the order given (most important
    first). Words Apple already indexes elsewhere in the storefront (name,
    subtitle, other localizations) and singular/plural duplicates are
    skipped, with the reason. Deterministic: the same input gives the same
    field, so the order of `candidates` is the priority.
    """
    seen = {w.lower() for w in (already_indexed or set())}
    result = PackResult(field="")
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if has_cjk(candidate):
            units = [candidate]
        elif split_phrases:
            units = words(candidate)
        else:
            units = [candidate]
        for unit in units:
            unit_words = [unit.lower()] if has_cjk(unit) else words(unit)
            if any(w in seen for w in unit_words):
                result.skipped.append((unit, "already indexed"))
                continue
            variant = next((plural_variant(w, seen) for w in unit_words if plural_variant(w, seen)), None)
            if variant:
                result.skipped.append((unit, f"singular/plural of '{variant}', already indexed"))
                continue
            trial = ",".join([*result.chosen, unit])
            if len(trial) > limit:
                result.did_not_fit.append(unit)
                continue
            result.chosen.append(unit)
            seen.update(unit_words)
    result.field = ",".join(result.chosen)
    return result
