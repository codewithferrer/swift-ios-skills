#!/usr/bin/env python3
"""Find localization gaps across everything an app shows in other languages:
the store listing fields, the app name and subtitle, each in-app purchase,
each subscription and subscription group, and each Game Center leaderboard
and achievement. Read-only.

The expected locales default to the ones on the version's store listing
(minus the app's `skip_locales`), so the report answers "which things are
missing a language the listing already supports". Pass --locales to compare
against a different set.

Usage:
    python audit_localizations.py --app my-app
    python audit_localizations.py --all
    python audit_localizations.py --app my-app --version 1.9.2 --locales en-US,es-ES,ja
    python audit_localizations.py --all --strict      # exit 1 when any gap exists
"""

from __future__ import annotations

import argparse
import sys

from asc_common import ConfigError, add_app_arguments, load_all_apps, load_client_module, resolve_app
from asc_status import Coverage, app_info_field_coverage, listing_field_coverage, render_coverage


def audit_app(client, app, version_string: str | None, locales: set | None) -> tuple[str, int]:
    versions = client.list_versions(app.app_id, limit=50)
    if not versions:
        return f"{app.key}: no versions found.", 0
    version = client.find_version(app.app_id, version_string) if version_string else versions[0]
    first_release = len(versions) == 1
    shown = version["attributes"].get("versionString")

    listing = client.list_localizations(version["id"])
    if locales:
        expected, source = set(locales), "(from --locales)"
    else:
        expected, source = set(listing) - app.skip_locales, f"(the locales on version {shown})"

    rows: list[Coverage] = listing_field_coverage(listing, expected, whats_new=not first_release)

    info = client.find_editable_app_info(app.app_id)
    if info is None:
        infos = client.list_app_infos(app.app_id)
        info = infos[0] if infos else None
    if info is not None:
        rows += app_info_field_coverage(client.list_app_info_localizations(info["id"]), expected)

    for iap in client.list_in_app_purchases(app.app_id):
        name = iap["attributes"].get("name") or iap["attributes"].get("productId")
        present = set(client.list_iap_localizations(iap["id"]))
        rows.append(Coverage("In-app purchase", str(name), present, expected))

    for sub in client.list_subscriptions(app.app_id):
        name = sub["attributes"].get("name") or sub["attributes"].get("productId")
        present = set(client.list_purchase_localizations("subscription", sub["id"]))
        rows.append(Coverage("Subscription", str(name), present, expected))

    for group in client.list_subscription_groups(app.app_id):
        name = group["attributes"].get("referenceName")
        present = set(client.list_purchase_localizations("subscription_group", group["id"]))
        rows.append(Coverage("Subscription group", str(name), present, expected))

    detail_id = client.find_game_center_detail_id(app.app_id)
    if detail_id:
        for kind, label, listing_fn in (
            ("gameCenterLeaderboards", "Leaderboard", client.list_leaderboards),
            ("gameCenterAchievements", "Achievement", client.list_achievements),
        ):
            for item in listing_fn(detail_id):
                present = set(client.list_game_center_localizations(kind, item["id"]))
                rows.append(Coverage(label, str(item["attributes"].get("referenceName")), present, expected))

    return render_coverage(app.key, source, expected, rows), sum(1 for r in rows if r.absent)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--all", action="store_true", help="Audit every app in the apps file")
    parser.add_argument("--version", help="Version whose listing defines the expected locales (default: newest)")
    parser.add_argument("--locales", help="Comma-separated locales to expect instead of the listing's")
    parser.add_argument("--strict", action="store_true", help="Exit with status 1 when any gap is found")
    args = parser.parse_args()

    try:
        apps = load_all_apps(args.apps_file) if args.all else [resolve_app(args)]
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    locales = {part.strip() for part in args.locales.split(",") if part.strip()} if args.locales else None

    try:
        client = asc.AppStoreConnectClient()
        reports, total_gaps = [], 0
        for app in apps:
            text, gaps = audit_app(client, app, args.version, locales)
            reports.append(text)
            total_gaps += gaps
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    print("\n\n".join(reports))
    return 1 if (args.strict and total_gaps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
