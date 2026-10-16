"""Local purchase text files <-> App Store Connect purchase localizations.

The display name and description that customers see for an in-app purchase,
for an auto-renewable subscription, and the display name of a subscription
group, one UTF-8 text file per field per locale:

    metadata/
      in_app_purchases/
        com.example.app.premium.lifetime/     the product ID
          en-US/
            name.txt  description.txt
          es-ES/
            ...
      subscriptions/
        com.example.app.premium.monthly/      the product ID
          en-US/
            name.txt  description.txt
      subscription_groups/
        Premium/                              the group's reference name
          en-US/
            name.txt  custom_app_name.txt

Texts only. The products and the group have to exist already (price,
availability, Family Sharing and the review screenshot are not handled here).

Pure logic only -- no network, no third-party imports -- so it can be unit
tested and used by --dry-run paths without credentials. `fetch_remote` talks
to whatever client object it is given, which is how the tests fake it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from asc_metadata import LOCALE_DIR_RE, normalize


@dataclass(frozen=True)
class Field:
    file: str            # file stem inside a locale directory
    attribute: str       # App Store Connect attribute name
    max_chars: int | None


@dataclass(frozen=True)
class Kind:
    key: str             # the client's name for it
    folder: str          # directory inside the metadata folder
    label: str           # for messages
    identified_by: str   # remote attribute that the directory name is matched against
    fields: tuple

    @property
    def field_by_file(self) -> dict:
        return {f.file: f for f in self.fields}


# The limits are Apple's published character limits for the display name (30)
# and the description (45) of a purchase. As elsewhere, they only reject text
# that is certainly too long before touching the API. The group's names have
# no limit here: Apple's own rules for them are not published as a plain
# number, so the API is left to say.
_PRODUCT_FIELDS = (
    Field("name", "name", 30),
    Field("description", "description", 45),
)

IN_APP_PURCHASE = Kind("in_app_purchase", "in_app_purchases", "In-app purchase", "productId", _PRODUCT_FIELDS)
SUBSCRIPTION = Kind("subscription", "subscriptions", "Subscription", "productId", _PRODUCT_FIELDS)
SUBSCRIPTION_GROUP = Kind(
    "subscription_group",
    "subscription_groups",
    "Subscription group",
    "referenceName",
    (Field("name", "name", None), Field("custom_app_name", "customAppName", None)),
)
KINDS = (IN_APP_PURCHASE, SUBSCRIPTION, SUBSCRIPTION_GROUP)
KINDS_BY_KEY = {k.key: k for k in KINDS}

# Localizations in these states are with Apple or already approved; the API
# may refuse to change them.
EDITABLE_STATE = "PREPARE_FOR_SUBMISSION"


# -- Local files ------------------------------------------------------------


@dataclass
class LocalPurchases:
    # (kind key, product id or group name) -> locale -> {file stem: text}
    items: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)   # things that were ignored, for display

    @property
    def localization_count(self) -> int:
        return sum(len(locales) for locales in self.items.values())


def read_purchases(
    directory: Path,
    only_locales: set | None = None,
    skip_locales: frozenset | set = frozenset(),
    only_products: set | None = None,
) -> LocalPurchases:
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} is not a directory -- see references/metadata-files.md for the layout.")

    result = LocalPurchases()
    for kind in KINDS:
        kind_dir = directory / kind.folder
        if not kind_dir.is_dir():
            continue
        specs = kind.field_by_file
        for product_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
            product = product_dir.name
            if only_products and product not in only_products:
                continue
            locales: dict = {}
            for locale_dir in sorted(p for p in product_dir.iterdir() if p.is_dir()):
                locale = locale_dir.name
                where = f"{kind.folder}/{product}/{locale}"
                if not LOCALE_DIR_RE.match(locale):
                    result.notes.append(f"{kind.folder}/{product}/{locale}: not a locale code, ignored")
                    continue
                if locale in skip_locales:
                    result.notes.append(f"{where}: skipped (listed in skip_locales)")
                    continue
                if only_locales and locale not in only_locales:
                    continue
                fields = {}
                for path in sorted(locale_dir.glob("*.txt")):
                    if path.stem not in specs:
                        result.notes.append(f"{where}/{path.name}: not a {kind.label.lower()} field, ignored")
                        continue
                    text = normalize(path.read_text(encoding="utf-8"))
                    if not text:
                        result.notes.append(f"{where}/{path.name}: empty, ignored")
                        continue
                    fields[path.stem] = text
                if fields:
                    locales[locale] = fields
            if locales:
                result.items[(kind.key, product)] = locales
    return result


def check_limits(items: dict) -> list[str]:
    problems = []
    for (kind_key, product), locales in sorted(items.items()):
        kind = KINDS_BY_KEY[kind_key]
        specs = kind.field_by_file
        for locale in sorted(locales):
            for stem, text in locales[locale].items():
                limit = specs[stem].max_chars
                if limit and len(text) > limit:
                    problems.append(
                        f"{kind.folder}/{product}/{locale}/{stem}.txt: {len(text)} characters, the limit is {limit}"
                    )
    return problems


# -- Remote side ---------------------------------------------------------------


@dataclass(frozen=True)
class RemoteItem:
    id: str
    localizations: dict   # locale -> API resource


def fetch_remote(client, app_id: str, wanted: set) -> dict:
    """(kind key, name) -> RemoteItem, for the purchases in `wanted`.

    Only the kinds that have local files are read, so an app with no
    subscriptions never has its subscription endpoints called. A name that is
    not found simply has no entry; `build_plan` reports it.
    """
    kinds = {kind_key for kind_key, _ in wanted}
    remote: dict = {}

    def collect(kind: Kind, resources: list) -> None:
        for resource in resources:
            name = resource["attributes"].get(kind.identified_by)
            if (kind.key, name) in wanted:
                locs = client.list_purchase_localizations(kind.key, resource["id"])
                remote[(kind.key, name)] = RemoteItem(resource["id"], locs)

    if IN_APP_PURCHASE.key in kinds:
        collect(IN_APP_PURCHASE, client.list_in_app_purchases(app_id))
    if SUBSCRIPTION.key in kinds:
        collect(SUBSCRIPTION, client.list_subscriptions(app_id))
    if SUBSCRIPTION_GROUP.key in kinds:
        collect(SUBSCRIPTION_GROUP, client.list_subscription_groups(app_id))
    return remote


# -- Diff against App Store Connect ----------------------------------------


@dataclass(frozen=True)
class Change:
    kind: Kind
    product: str
    locale: str
    field: Field
    old: str | None
    new: str
    new_locale: bool
    state: str | None   # the remote localization's state, None for a new locale


@dataclass
class Plan:
    changes: list = field(default_factory=list)     # Change, to be written
    unchanged: int = 0                              # fields that already match
    problems: list = field(default_factory=list)    # str, blocks the whole run
    warnings: list = field(default_factory=list)    # str, shown but not blocking


def build_plan(items: dict, remote: dict) -> Plan:
    plan = Plan()
    for (kind_key, product), locales in sorted(items.items()):
        kind = KINDS_BY_KEY[kind_key]
        item = remote.get((kind_key, product))
        if item is None:
            plan.problems.append(
                f"{kind.label} '{product}' is not in App Store Connect. This script writes texts only: "
                "create it there first, then run it again."
            )
            continue
        for locale in sorted(locales):
            texts = locales[locale]
            existing = item.localizations.get(locale)
            if existing is None:
                if "name" not in texts:
                    plan.problems.append(
                        f"{kind.folder}/{product}/{locale}: this locale does not exist yet, so name.txt is required"
                    )
                    continue
                if kind.key != SUBSCRIPTION_GROUP.key and "description" not in texts:
                    plan.warnings.append(
                        f"{kind.folder}/{product}/{locale}: no description.txt, so the new locale will have "
                        "none and App Store Connect may keep the product at Missing Metadata"
                    )
            state = existing["attributes"].get("state") if existing else None
            for spec in kind.fields:
                new = texts.get(spec.file)
                if new is None:
                    continue
                old = existing["attributes"].get(spec.attribute) if existing else None
                if existing is not None and normalize(old) == new:
                    plan.unchanged += 1
                else:
                    plan.changes.append(Change(kind, product, locale, spec, old, new, existing is None, state))
    return plan


def describe_change(change: Change) -> str:
    """One line. Short values are shown whole; long ones as character counts."""
    old = normalize(change.old)
    if max(len(old), len(change.new)) <= 100:
        return f'"{old}" -> "{change.new}"' if old else f'(empty) -> "{change.new}"'
    return f"{len(old)} -> {len(change.new)} characters"


def locked_changes(plan: Plan) -> list:
    """Changes aimed at a localization that is with Apple or already approved."""
    return [c for c in plan.changes if c.state not in (None, EDITABLE_STATE)]
