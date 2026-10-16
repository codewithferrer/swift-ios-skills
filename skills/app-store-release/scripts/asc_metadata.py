"""Local metadata files <-> App Store Connect localizations.

The on-disk layout is the one fastlane's `deliver` uses, so an existing
fastlane `metadata/` folder works as-is:

    metadata/
      en-US/
        name.txt  subtitle.txt  privacy_url.txt        app-level fields
        description.txt  keywords.txt  promotional_text.txt
        support_url.txt  marketing_url.txt              per-version fields
      es-ES/
        ...

One UTF-8 text file per field per locale, named with App Store Connect locale
codes. "What's New" is deliberately not part of this: it has its own source
(RELEASE_NOTES.md) and script (push_whats_new.py).

Pure logic only -- no network, no third-party imports -- so it can be unit
tested and used by --dry-run paths without credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

APP_INFO = "app_info"  # appInfoLocalizations: shared across versions
VERSION = "version"    # appStoreVersionLocalizations: belongs to one version


@dataclass(frozen=True)
class Field:
    file: str            # file stem inside a locale directory
    level: str           # APP_INFO or VERSION
    attribute: str       # App Store Connect attribute name
    max_chars: int | None


# Limits are Apple's published character limits. They are only used to
# reject text that is certainly too long before touching the API; anything
# that slips through (e.g. a byte-based rule) is reported by the API itself.
FIELDS = (
    Field("name", APP_INFO, "name", 30),
    Field("subtitle", APP_INFO, "subtitle", 30),
    Field("privacy_url", APP_INFO, "privacyPolicyUrl", None),
    Field("description", VERSION, "description", 4000),
    Field("keywords", VERSION, "keywords", 100),
    Field("promotional_text", VERSION, "promotionalText", 170),
    Field("support_url", VERSION, "supportUrl", None),
    Field("marketing_url", VERSION, "marketingUrl", None),
)
FIELDS_BY_FILE = {f.file: f for f in FIELDS}

# en-US, es-MX, zh-Hans, ja, ... -- keeps fastlane's `default/`,
# `screenshots/` and `review_information/` directories out of the way.
LOCALE_DIR_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,4})*$")

# Folders of the purchase texts (asc_purchases.py), which share this metadata
# folder but are none of this module's business.
PURCHASE_FOLDERS = frozenset({"in_app_purchases", "subscriptions", "subscription_groups"})


def normalize(text: str | None) -> str:
    """Comparison form: Windows line endings folded, surrounding whitespace
    dropped. Files conventionally end with a newline that Apple does not
    store, so it must not count as a difference.
    """
    return (text or "").replace("\r\n", "\n").strip()


# -- Local files ------------------------------------------------------------


@dataclass
class LocalMetadata:
    texts: dict = field(default_factory=dict)   # locale -> {file stem: text}
    notes: list = field(default_factory=list)   # things that were ignored, for display


def read_local(
    directory: Path,
    only_locales: set | None = None,
    skip_locales: frozenset | set = frozenset(),
    only_fields: set | None = None,
) -> LocalMetadata:
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a directory -- see references/metadata-files.md for the layout.")

    result = LocalMetadata()
    ignored_dirs = []
    for locale_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        locale = locale_dir.name
        if locale in PURCHASE_FOLDERS:
            continue  # push_purchases.py reads these
        if not LOCALE_DIR_RE.match(locale):
            ignored_dirs.append(locale)
            continue
        if locale in skip_locales:
            result.notes.append(f"{locale}: skipped (listed in skip_locales)")
            continue
        if only_locales and locale not in only_locales:
            continue

        fields = {}
        for path in sorted(locale_dir.glob("*.txt")):
            spec = FIELDS_BY_FILE.get(path.stem)
            if spec is None:
                hint = " -- What's New is pushed by push_whats_new.py" if path.stem == "release_notes" else ""
                result.notes.append(f"{locale}/{path.name}: not a metadata field, ignored{hint}")
                continue
            if only_fields and path.stem not in only_fields:
                continue
            text = normalize(path.read_text(encoding="utf-8"))
            if not text:
                result.notes.append(f"{locale}/{path.name}: empty, ignored")
                continue
            fields[path.stem] = text
        if fields:
            result.texts[locale] = fields

    if ignored_dirs:
        result.notes.append(f"not locale codes, ignored: {', '.join(ignored_dirs)}")
    return result


def check_limits(texts: dict) -> list[str]:
    problems = []
    for locale in sorted(texts):
        for stem, text in texts[locale].items():
            limit = FIELDS_BY_FILE[stem].max_chars
            if limit and len(text) > limit:
                problems.append(f"{locale}/{stem}.txt: {len(text)} characters, the limit is {limit}")
    return problems


# -- Diff against App Store Connect ----------------------------------------


@dataclass(frozen=True)
class Change:
    locale: str
    field: Field
    old: str | None   # None when the locale does not exist remotely yet
    new: str
    kind: str         # "changed" or "new locale"


@dataclass
class Plan:
    changes: list = field(default_factory=list)      # Change, to be written
    unchanged: list = field(default_factory=list)    # (locale, Field), already identical
    unavailable: list = field(default_factory=list)  # (locale, Field), no editable app info
    problems: list = field(default_factory=list)     # str, blocks the whole run


def build_plan(texts: dict, version_locs: dict, info_locs: dict | None) -> Plan:
    """`version_locs` / `info_locs` map locale -> API resource. `info_locs`
    is None when the app has no editable app info, which makes every
    app-level field unavailable instead of a failed write.
    """
    plan = Plan()
    for locale in sorted(texts):
        for stem, new in texts[locale].items():
            spec = FIELDS_BY_FILE[stem]
            remote_locs = version_locs if spec.level == VERSION else info_locs
            if remote_locs is None:
                plan.unavailable.append((locale, spec))
                continue
            remote = remote_locs.get(locale)
            old = remote["attributes"].get(spec.attribute) if remote else None
            if remote is not None and normalize(old) == new:
                plan.unchanged.append((locale, spec))
            else:
                kind = "changed" if remote is not None else "new locale"
                plan.changes.append(Change(locale, spec, old, new, kind))

        # An app info localization cannot be created without a name.
        if info_locs is not None and locale not in info_locs:
            app_level = [s for s in texts[locale] if FIELDS_BY_FILE[s].level == APP_INFO]
            if app_level and "name" not in texts[locale]:
                plan.problems.append(
                    f"{locale}: this locale has no app info yet, so name.txt is required "
                    f"alongside {', '.join(f + '.txt' for f in app_level)}"
                )
    return plan


def describe_change(change: Change) -> str:
    """One line. Short values are shown whole; long ones as character counts."""
    old = normalize(change.old)
    if max(len(old), len(change.new)) <= 100:
        return f'"{old}" -> "{change.new}"' if old else f'(empty) -> "{change.new}"'
    return f"{len(old)} -> {len(change.new)} characters"


# -- Pull ---------------------------------------------------------------------


def remote_texts(
    version_locs: dict,
    info_locs: dict | None,
    only_locales: set | None = None,
    skip_locales: frozenset | set = frozenset(),
    only_fields: set | None = None,
) -> dict:
    """locale -> {file stem: text} for every non-empty field App Store
    Connect currently holds. Empty fields are left out so no empty files
    are written.
    """
    texts: dict = {}
    for spec in FIELDS:
        if only_fields and spec.file not in only_fields:
            continue
        source = version_locs if spec.level == VERSION else info_locs
        for locale, resource in (source or {}).items():
            if locale in skip_locales or (only_locales and locale not in only_locales):
                continue
            value = normalize(resource["attributes"].get(spec.attribute))
            if value:
                texts.setdefault(locale, {})[spec.file] = value
    return texts


# -- Version-level extras that are not per locale -----------------------------------
#
# Same layout as fastlane's `deliver`:
#
#     metadata/
#       copyright.txt                  the version's copyright line
#       review_information/
#         first_name.txt  last_name.txt  phone_number.txt  email_address.txt
#         demo_user.txt  demo_password.txt  notes.txt

COPYRIGHT_GROUP = "copyright"
REVIEW_GROUP = "review_information"
GLOBAL_GROUPS = (COPYRIGHT_GROUP, REVIEW_GROUP)

# file stem -> appStoreReviewDetails attribute
REVIEW_FIELDS = {
    "first_name": "contactFirstName",
    "last_name": "contactLastName",
    "phone_number": "contactPhone",
    "email_address": "contactEmail",
    "demo_user": "demoAccountName",
    "demo_password": "demoAccountPassword",
    "notes": "notes",
}
# Never printed, never written back into files by a pull.
SECRET_REVIEW_FIELDS = frozenset({"demo_password"})
REVIEW_NOTES_LIMIT = 4000


@dataclass
class GlobalMetadata:
    copyright: str | None = None
    review: dict = field(default_factory=dict)   # file stem -> text
    notes: list = field(default_factory=list)
    paths: dict = field(default_factory=dict)    # file stem -> Path, for checks on the files themselves


def read_global(directory: Path) -> GlobalMetadata:
    result = GlobalMetadata()
    copyright_file = directory / f"{COPYRIGHT_GROUP}.txt"
    if copyright_file.is_file():
        text = normalize(copyright_file.read_text(encoding="utf-8"))
        if text:
            result.copyright = text
        else:
            result.notes.append("copyright.txt: empty, ignored")

    review_dir = directory / REVIEW_GROUP
    if review_dir.is_dir():
        for path in sorted(review_dir.glob("*.txt")):
            if path.stem not in REVIEW_FIELDS:
                result.notes.append(f"{REVIEW_GROUP}/{path.name}: not a review information field, ignored")
                continue
            text = normalize(path.read_text(encoding="utf-8"))
            if not text:
                result.notes.append(f"{REVIEW_GROUP}/{path.name}: empty, ignored")
                continue
            result.review[path.stem] = text
            result.paths[path.stem] = path
    return result


def review_attributes(review: dict) -> tuple[dict, list[str], list[str]]:
    """(API attributes, blocking problems, warnings) for the review contact
    files. The demo account is only marked as required when both its name and
    its password are given.
    """
    attributes: dict = {REVIEW_FIELDS[stem]: text for stem, text in review.items() if stem in REVIEW_FIELDS}
    problems, warnings = [], []

    user, password = review.get("demo_user"), review.get("demo_password")
    if bool(user) != bool(password):
        problems.append(f"{REVIEW_GROUP}: demo_user.txt and demo_password.txt must be given together")
    elif user and password:
        attributes["demoAccountRequired"] = True

    email = review.get("email_address")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        problems.append(f"{REVIEW_GROUP}/email_address.txt: \"{email}\" does not look like an email address")
    notes = review.get("notes")
    if notes and len(notes) > REVIEW_NOTES_LIMIT:
        problems.append(f"{REVIEW_GROUP}/notes.txt: {len(notes)} characters, the limit is {REVIEW_NOTES_LIMIT}")
    phone = review.get("phone_number")
    if phone and not phone.startswith("+"):
        warnings.append(f"{REVIEW_GROUP}/phone_number.txt: include the country code, for example +34 600 000 000")
    return attributes, problems, warnings


@dataclass(frozen=True)
class GlobalChange:
    group: str                  # COPYRIGHT_GROUP or REVIEW_GROUP
    label: str                  # what to show
    attribute: str              # API attribute
    old: object
    new: object
    secret: bool = False


def plan_globals(
    copyright_text: str | None,
    current_copyright: str | None,
    review_attributes_: dict,
    current_review: dict | None,
) -> tuple[list[GlobalChange], int]:
    """Changes needed for the copyright line and the review contact, and how
    many provided values already match. `current_review` is the attributes of
    the existing App Review information, or None when it does not exist yet.
    """
    changes: list[GlobalChange] = []
    unchanged = 0
    if copyright_text is not None:
        if normalize(current_copyright) != copyright_text:
            changes.append(GlobalChange(COPYRIGHT_GROUP, "copyright", "copyright", current_copyright, copyright_text))
        else:
            unchanged += 1

    by_attribute = {attr: stem for stem, attr in REVIEW_FIELDS.items()}
    by_attribute["demoAccountRequired"] = "demo_account_required"
    for attribute, new in review_attributes_.items():
        label = by_attribute[attribute]
        old = (current_review or {}).get(attribute)
        same = (bool(old) is True) if isinstance(new, bool) else (normalize(old) == new)
        if same and current_review is not None:
            unchanged += 1
        else:
            changes.append(GlobalChange(REVIEW_GROUP, label, attribute, old, new,
                                        secret=label in SECRET_REVIEW_FIELDS))
    return changes, unchanged


def describe_global(change: GlobalChange) -> str:
    if change.secret:
        return "(set)" if change.old else "(empty) -> (set)"
    old = "(empty)" if change.old in (None, "") else f"\"{change.old}\""
    new = change.new if isinstance(change.new, bool) else f"\"{change.new}\""
    return f"{old} -> {new}"
