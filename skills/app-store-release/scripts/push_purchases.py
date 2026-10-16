#!/usr/bin/env python3
"""Push the texts customers see for an app's in-app purchases and
subscriptions from local files (see asc_purchases.py for the layout) to App
Store Connect: the display name and description of each in-app purchase and of
each auto-renewable subscription, and the display name (and optional custom app
name) of each subscription group, for every locale folder found.

Texts only. The products and the group must already exist in App Store
Connect: creating them, their price, availability, Family Sharing and the
review screenshot are not handled here.

It compares each field with what App Store Connect holds and only writes the
ones that differ. A locale the product does not have yet is created (that needs
a name.txt). Text that is certainly over Apple's character limits is rejected
before any API call. Nothing is written without a confirmation, and --dry-run
only prints the comparison.

Purchases are not tied to an app version, so there is no --version.

Usage:
    python push_purchases.py --app my-app --dry-run
    python push_purchases.py --app my-app
    python push_purchases.py --app my-app --products com.example.app.premium.lifetime --locales en-US
    python push_purchases.py --app my-app --yes      # skip the confirmation prompt
"""

from __future__ import annotations

import argparse
import sys

from asc_common import (
    ConfigError,
    add_app_arguments,
    confirm,
    display_path,
    load_client_module,
    resolve_app,
    resolve_metadata_dir,
    selected_locales,
)
from asc_purchases import (
    EDITABLE_STATE,
    KINDS_BY_KEY,
    build_plan,
    check_limits,
    describe_change,
    fetch_remote,
    locked_changes,
    read_purchases,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--dir", help="Metadata folder (default: the app's metadata_path in the apps file)")
    parser.add_argument("--locales", help="Comma-separated locales to include, e.g. en-US,es-ES (default: all)")
    parser.add_argument(
        "--products",
        help="Comma-separated product IDs (or subscription group names) to include (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compare and print, make no changes")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
        source = resolve_metadata_dir(args, app)
        only_locales = selected_locales(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    only_products = {p.strip() for p in (args.products or "").split(",") if p.strip()} or None

    if not source.is_dir():
        print(f"{source} is not a directory -- see references/metadata-files.md for the layout.", file=sys.stderr)
        return 1
    local = read_purchases(source, only_locales, app.skip_locales, only_products)
    if not local.items:
        print(f"No purchase files found in {source} for this selection.", file=sys.stderr)
        for note in local.notes:
            print(f"  {note}", file=sys.stderr)
        return 1

    problems = check_limits(local.items)
    if problems:
        print("Cannot send these -- nothing was sent:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    try:
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()
        remote = fetch_remote(client, app.app_id, set(local.items))
        plan = build_plan(local.items, remote)

        print(f"App: {app.key} (id {app.app_id})")
        print(f"Source: {display_path(source)} ({len(local.items)} product(s), "
              f"{local.localization_count} localization(s))")
        print()

        if plan.problems:
            print("Cannot continue -- nothing was sent:", file=sys.stderr)
            for problem in plan.problems:
                print(f"  {problem}", file=sys.stderr)
            return 1

        if plan.changes:
            print(f"Changes ({len(plan.changes)}):")
            last = None
            for change in plan.changes:
                heading = (change.kind.label, change.product)
                if heading != last:
                    print(f"{change.kind.label} {change.product}")
                    last = heading
                suffix = "  [new locale]" if change.new_locale else ""
                print(f"  {change.locale:<8} {change.field.file:<16} {describe_change(change)}{suffix}")
        else:
            print("No changes: everything already matches App Store Connect.")
        print(f"Unchanged: {plan.unchanged} field(s).")
        for note in local.notes:
            print(f"Note: {note}")
        for warning in plan.warnings:
            print(f"Warning: {warning}")
        locked = locked_changes(plan)
        if locked:
            states = sorted({c.state for c in locked})
            print(f"Warning: {len(locked)} change(s) target a localization that is not in {EDITABLE_STATE} "
                  f"({', '.join(states)}); Apple may refuse them.")
        print()

        if not plan.changes:
            return 0
        if args.dry_run:
            print("Dry run -- no changes made.")
            return 0
        if not confirm("Push these changes to App Store Connect now?", args.yes):
            return 1

        grouped: dict = {}
        for change in plan.changes:
            grouped.setdefault((change.kind.key, change.product, change.locale), []).append(change)
        for (kind_key, product, locale), changes in grouped.items():
            item = remote[(kind_key, product)]
            attributes = {c.field.attribute: c.new for c in changes}
            if changes[0].new_locale:
                client.create_purchase_localization(kind_key, item.id, locale, attributes)
            else:
                client.update_purchase_localization(kind_key, item.localizations[locale]["id"], attributes)
            written = ", ".join(c.field.file for c in changes)
            print(f"{KINDS_BY_KEY[kind_key].label} {product} {locale}: wrote {written}")
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        print("Anything listed above was already written; the rest was not.", file=sys.stderr)
        return 1

    print("\nDone. Run audit_localizations.py to see which languages are still missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
