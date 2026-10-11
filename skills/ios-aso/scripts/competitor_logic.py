"""Analysis of a market segment from public App Store data. Pure logic: no
network, no third-party imports, so each rule can be unit tested with
fixtures.

What this can and cannot say, plainly:

- It sees what is public: names, ratings, languages, screenshots, price,
  update dates and written reviews. It does NOT see downloads, revenue,
  conversion, or any competitor's hidden keyword field, so it cannot tell you
  what *works*, only what the visible competitors do and which of them rank
  above their weight.
- The search order it uses is the one the public Search API returns, a proxy
  for ranking and not a guarantee.
- Every string that comes from an app listing or a review is untrusted text.
  `clean()` strips control characters and truncates, and renderers label
  such text so it is read as data and never as instructions.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

STOPWORDS = {
    "a", "an", "and", "the", "for", "to", "of", "in", "on", "with", "by", "your", "you", "my", "app", "apps",
    "is", "it", "at", "or", "as", "vs", "new", "free", "no", "el", "la", "los", "las", "de", "del", "y", "para",
    "con", "tu", "mi", "un", "una", "en", "por", "der", "die", "das", "und", "für", "mit", "le", "les", "des",
    "et", "pour", "avec", "du", "au",
}
REVIEW_FILLER = STOPWORDS | {
    "this", "that", "have", "has", "was", "are", "but", "not", "can", "get", "got", "just", "very", "really",
    "will", "would", "when", "they", "them", "their", "from", "all", "out", "more", "make", "any", "been",
    "game", "love", "great", "good", "like", "one", "use", "time", "also", "only", "even", "much", "than",
    "pero", "muy", "esta", "este", "que", "los", "las", "como", "más", "juego", "ist", "nicht", "aber", "pas",
    # contractions, once the apostrophe is removed: they carry sentiment, not a theme
    "dont", "didnt", "doesnt", "wont", "wouldnt", "couldnt", "shouldnt", "cant", "isnt", "arent", "wasnt",
    "havent", "hasnt", "ive", "im", "its", "thats", "theres",
}
APOSTROPHE_RE = re.compile(r"[\u2019\u2018'`\u00b4]")
SPLIT_NAME_RE = re.compile(r"\s+[:\-–—|•]\s+|:\s+")
WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

# Storefront code -> the language code Apple uses in languageCodesISO2A.
STOREFRONT_LANGUAGE = {
    "us": "EN", "gb": "EN", "ca": "EN", "au": "EN", "es": "ES", "mx": "ES", "jp": "JA", "kr": "KO", "cn": "ZH",
    "tw": "ZH", "hk": "ZH", "sa": "AR", "ae": "AR", "de": "DE", "fr": "FR", "it": "IT", "br": "PT", "pt": "PT",
    "nl": "NL", "ru": "RU", "tr": "TR", "in": "EN", "ar": "ES", "co": "ES", "cl": "ES", "pe": "ES",
    "at": "DE", "ch": "DE", "ie": "EN", "nz": "EN", "sg": "EN",
}
# Note: "ar" here is Argentina (a storefront), not the Arabic language code.

# An app ranks "above its weight" when it is in the top results of a term
# with at most this fraction of the cohort's median number of ratings.
OVERPERFORMER_RATIO = 0.25
# Below this median the cohort is mostly unrated newcomers: there is no
# incumbent to be "above", and flagging every unrated app would say nothing.
MIN_MEDIAN_FOR_OVERPERFORMERS = 20


# -- Text safety ---------------------------------------------------------------


def clean(text: str | None, limit: int = 160) -> str:
    """One line, no control characters, truncated. Untrusted text goes
    through here before it is printed.
    """
    text = CONTROL_RE.sub(" ", text or "")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# -- Apps -------------------------------------------------------------------------


@dataclass
class App:
    id: str
    name: str
    seller: str
    genre: str
    rating: float
    rating_count: int
    price_text: str
    free: bool
    languages: list = field(default_factory=list)
    iphone_shots: int = 0
    ipad_shots: int = 0
    size_mb: float | None = None
    game_center: bool = False
    version: str = ""
    last_update: datetime | None = None
    released: datetime | None = None
    url: str = ""
    description: str = ""
    release_notes: str = ""

    def age_days(self, now: datetime) -> int | None:
        return (now - self.last_update).days if self.last_update else None


def normalize_app(raw: dict) -> App:
    size = raw.get("fileSizeBytes")
    return App(
        id=str(raw.get("trackId", "")),
        name=raw.get("trackName") or raw.get("trackCensoredName") or "",
        seller=raw.get("sellerName") or raw.get("artistName") or "",
        genre=raw.get("primaryGenreName") or "",
        rating=float(raw.get("averageUserRating") or 0),
        rating_count=int(raw.get("userRatingCount") or 0),
        price_text=raw.get("formattedPrice") or "",
        free=float(raw.get("price") or 0) == 0,
        languages=list(raw.get("languageCodesISO2A") or []),
        iphone_shots=len(raw.get("screenshotUrls") or []),
        ipad_shots=len(raw.get("ipadScreenshotUrls") or []),
        size_mb=round(int(size) / 1_000_000, 1) if str(size or "").isdigit() else None,
        game_center=bool(raw.get("isGameCenterEnabled")),
        version=raw.get("version") or "",
        last_update=parse_date(raw.get("currentVersionReleaseDate")),
        released=parse_date(raw.get("releaseDate")),
        url=raw.get("trackViewUrl") or "",
        description=raw.get("description") or "",
        release_notes=raw.get("releaseNotes") or "",
    )


# -- Landscape ---------------------------------------------------------------------


@dataclass
class LandscapeRow:
    app: App
    ranks: dict  # term -> 1-based rank within the top N

    @property
    def terms_hit(self) -> int:
        return len(self.ranks)

    @property
    def mean_rank(self) -> float:
        return sum(self.ranks.values()) / len(self.ranks)


def landscape(results: dict[str, list[App]], top: int = 10) -> list[LandscapeRow]:
    """Apps in the top `top` of at least one term, most widely present first.
    An app that shows up for several of the terms you care about is a
    competitor for the segment, not just for one phrase.
    """
    rows: dict[str, LandscapeRow] = {}
    for term, apps in results.items():
        for rank, app in enumerate(apps[:top], start=1):
            row = rows.setdefault(app.id, LandscapeRow(app, {}))
            row.ranks[term] = rank
    return sorted(rows.values(), key=lambda r: (-r.terms_hit, r.mean_rank, r.app.name))


def cohort(results: dict[str, list[App]], top: int = 10) -> list[App]:
    """Unique apps across the top `top` of every term."""
    return [row.app for row in landscape(results, top)]


def overperformers(results: dict[str, list[App]], top: int = 10) -> list[tuple[App, str, int]]:
    """Apps in the top results of a term whose rating count is far below the
    cohort's median. Their listing is doing work that brand and install base
    are not, which makes them the most instructive to study.
    """
    apps = cohort(results, top)
    counts = [a.rating_count for a in apps]
    if len(counts) < 5:
        return []
    median = statistics.median(counts)
    if median < MIN_MEDIAN_FOR_OVERPERFORMERS:
        return []
    out = []
    for term, listed in results.items():
        for rank, app in enumerate(listed[:top], start=1):
            if app.rating_count <= median * OVERPERFORMER_RATIO and (app, term, rank) not in out:
                out.append((app, term, rank))
    return sorted(out, key=lambda x: (x[2], x[0].rating_count))


def name_match_share(listed: list[App], term: str, top: int = 10) -> tuple[int, int]:
    """(apps whose name contains every word of the term, apps considered).
    The subtitle is not part of the public API, so this is a lower bound:
    an app may match through its subtitle or hidden keywords.

    Languages written without spaces (Japanese, Chinese) cannot be split into
    words, so a term there is matched as a substring of the name.
    """
    wanted = [w.lower() for w in WORD_RE.findall(term) if w.lower() not in STOPWORDS]
    considered = listed[:top]
    if not wanted:
        return 0, len(considered)

    def matches(name: str) -> bool:
        lowered = name.lower()
        tokens = {w.lower() for w in WORD_RE.findall(name)}
        return all((w in lowered) if CJK_RE.search(w) else (w in tokens) for w in wanted)

    return sum(1 for a in considered if matches(a.name)), len(considered)


def stale_in_top(results: dict[str, list[App]], now: datetime, top: int = 10, days: int = 365) -> list[tuple[App, int]]:
    """Apps in the top results that have not shipped an update for `days` or
    more: a competitor you can overtake on freshness and polish.
    """
    out: dict[str, tuple[App, int]] = {}
    for listed in results.values():
        for app in listed[:top]:
            age = app.age_days(now)
            if age is not None and age >= days:
                out[app.id] = (app, age)
    return sorted(out.values(), key=lambda x: -x[1])


def new_entrants(results: dict[str, list[App]], now: datetime, top: int = 10, within_days: int = 180) -> list[App]:
    """Apps first released recently that already sit in the top results."""
    out: dict[str, App] = {}
    for listed in results.values():
        for app in listed[:top]:
            if app.released and (now - app.released).days <= within_days:
                out[app.id] = app
    return sorted(out.values(), key=lambda a: a.released, reverse=True)


# -- Cohort statistics -----------------------------------------------------------


def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def cohort_stats(apps: list[App], now: datetime) -> dict:
    rated = [a for a in apps if a.rating_count > 0]
    return {
        "apps": len(apps),
        "median_rating": _median([a.rating for a in rated]),
        "median_rating_count": _median([a.rating_count for a in apps]),
        "median_languages": _median([len(a.languages) for a in apps]),
        "median_iphone_screenshots": _median([a.iphone_shots for a in apps]),
        "median_days_since_update": _median([a.age_days(now) for a in apps]),
        "share_free": sum(a.free for a in apps) / len(apps) if apps else None,
        "share_game_center": sum(a.game_center for a in apps) / len(apps) if apps else None,
        "genres": Counter(a.genre for a in apps).most_common(4),
    }


def position(value: float | None, others: list[float]) -> str:
    """Where `value` sits among `others`, in words."""
    others = [o for o in others if o is not None]
    if value is None or not others:
        return "n/a"
    below = sum(1 for o in others if o < value)
    equal = sum(1 for o in others if o == value)
    pct = round(100 * (below + equal / 2) / len(others))
    return f"{pct}th percentile of {len(others)}"


# -- Names -------------------------------------------------------------------------


def split_name(name: str) -> tuple[str, str | None]:
    """('Brand', 'descriptor') for "Brand: descriptor" style names."""
    parts = SPLIT_NAME_RE.split(name, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return name.strip(), None


def name_tokens(name: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(name) if w.lower() not in STOPWORDS]


def name_vocabulary(apps: list[App], min_apps: int = 3, exclude: set | None = None) -> list[tuple[str, int]]:
    """Words used in the names of at least `min_apps` apps, minus `exclude`
    (words you already have in your own name, subtitle or keywords).
    A word is counted once per app. Frequent words are the vocabulary of
    the segment: a bet many competitors made, not proof that it pays.
    """
    exclude = {w.lower() for w in (exclude or set())}
    counts: Counter = Counter()
    for app in apps:
        counts.update(set(name_tokens(app.name)))
    return [(w, c) for w, c in counts.most_common() if c >= min_apps and w not in exclude]


def name_patterns(apps: list[App]) -> dict:
    with_descriptor = sum(1 for a in apps if split_name(a.name)[1])
    lengths = [len(a.name) for a in apps]
    return {
        "median_length": _median(lengths),
        "at_limit": sum(1 for n in lengths if n >= 28),
        "with_descriptor": with_descriptor,
        "total": len(apps),
    }


# -- Reviews -----------------------------------------------------------------------------


@dataclass
class Review:
    rating: int
    title: str
    content: str
    version: str
    updated: str


def normalize_review(entry: dict) -> Review:
    def label(key: str) -> str:
        node = entry.get(key) or {}
        return node.get("label", "") if isinstance(node, dict) else str(node)

    try:
        rating = int(label("im:rating"))
    except ValueError:
        rating = 0
    return Review(rating, label("title"), label("content"), label("im:version"), label("updated"))


def review_summary(reviews: list[Review]) -> dict:
    n = len(reviews)
    hist = Counter(r.rating for r in reviews)
    return {
        "n": n,
        "average": round(sum(r.rating for r in reviews) / n, 2) if n else None,
        "histogram": {star: hist.get(star, 0) for star in (5, 4, 3, 2, 1)},
        "low_share": round(sum(1 for r in reviews if 0 < r.rating <= 2) / n, 2) if n else None,
        "versions": Counter(r.version for r in reviews if r.version).most_common(3),
    }


def review_words(reviews: list[Review], low: bool, top: int = 12) -> list[tuple[str, int]]:
    """Words by number of reviews that use them, for low (<=2) or high (>=4)
    ratings, with filler removed. A first pass for themes, not an analysis.
    """
    chosen = [r for r in reviews if (0 < r.rating <= 2 if low else r.rating >= 4)]
    counts: Counter = Counter()
    for review in chosen:
        text = APOSTROPHE_RE.sub("", f"{review.title} {review.content}".lower())
        counts.update({w for w in WORD_RE.findall(text) if w not in REVIEW_FILLER and len(w) > 2})
    return counts.most_common(top)


# -- Comparing storefronts -----------------------------------------------------------


def market_summary(results: dict[str, list[App]], now: datetime, language: str | None, top: int = 10) -> dict:
    """Comparable signals for one storefront. They describe the *visible
    competition*, not the size of the market: nothing here knows downloads.
    """
    apps = cohort(results, top)
    rows = landscape(results, top)
    shares = [name_match_share(listed, term, top) for term, listed in results.items()]
    considered = sum(n for _, n in shares)
    return {
        "apps": len(apps),
        "median_ratings": _median([a.rating_count for a in apps]),
        "median_languages": _median([len(a.languages) for a in apps]),
        "local_language_share": (sum(1 for a in apps if language in a.languages) / len(apps)) if apps and language else None,
        "name_match_share": (sum(h for h, _ in shares) / considered) if considered else None,
        "above_weight": len(overperformers(results, top)) if (_median([a.rating_count for a in apps]) or 0)
        >= MIN_MEDIAN_FOR_OVERPERFORMERS else None,
        "stale": len(stale_in_top(results, now, top)),
        "new_entrants": len(new_entrants(results, now, top)),
        "top": [r.app for r in rows[:3]],
        "ids": {a.id for a in apps},
    }


def local_only_share(summaries: dict[str, dict]) -> dict[str, float | None]:
    """Per market, the share of its cohort that appears in no other market's
    cohort: high where local players dominate, low where global apps do.
    """
    out: dict[str, float | None] = {}
    for code, s in summaries.items():
        others = set().union(*(o["ids"] for c, o in summaries.items() if c != code)) if len(summaries) > 1 else set()
        out[code] = (len(s["ids"] - others) / len(s["ids"])) if s["ids"] and len(summaries) > 1 else None
    return out
