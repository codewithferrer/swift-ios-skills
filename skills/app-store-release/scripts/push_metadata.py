#!/usr/bin/env python3
"""Push an app's store listing text from local metadata files (see
asc_metadata.py for the layout) to App Store Connect: name, subtitle,
privacy policy URL, description, keywords, promotional text, support URL and
marketing URL, for every locale folder found.

It compares each field with what App Store Connect holds and only writes the
ones that differ. Text that is certainly over Apple's character limits is
rejected before any API call. "What's New" is not handled here; see
push_whats_new.py.

Per-version fields (description, keywords, promotional text, URLs) need the
version to exist and be editable. App-level fields (name, subtitle, privacy
URL) need an app info in an editable state, which Apple exposes while a
version is being prepared; when there is none they are reported and skipped,
and the per-version fields are still pushed.

Run pull_metadata.py first if you want a backup of what is live.

Usage:
    python push_metadata.py --app my-app --version 1.9.3 --dry-run
    python push_metadata.py --app my-app --version 1.9.3
    python push_metadata.py --app my-app --version 1.9.3 --fields promotional_text --locales en-US
    python push_metadata.py --app my-app --version 1.9.3 --yes   # skip the confirmation prompt
"""

from __future__ import annotations

import argparse
import sys

from asc_common import (
    ConfigError,
    add_app_arguments,
    add_metadata_arguments,
    confirm,
    display_path,
    load_client_module,
    resolve_app,
    resolve_metadata_dir,
    selected_fields,
    selected_locales,
)
from asc_metadata import (
    APP_INFO,
    FIELDS_BY_FILE,
    VERSION,
    build_plan,
    check_limits,
    describe_change,
    normalize,
    read_local,
)

LIVE_VERSION_STATES = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    add_metadata_arguments(parser)
    parser.add_argument("--version", required=True, help="Exact marketing version string, e.g. 1.9.3")
    parser.add_argument("--dry-run", action="store_true", help="Compare and print, make no changes")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
        source = resolve_metadata_dir(args, app)
        only_locales = selected_locales(args)
        only_fields = selected_fields(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        local = read_local(source, only_locales, app.skip_locales, only_fields)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not local.texts:
        print(f"No metadata files found in {source} for this selection.", file=sys.stderr)
        for note in local.notes:
            print(f"  {note}", file=sys.stderr)
        return 1

    problems = check_limits(local.texts)
    if problems:
        print("Over Apple's character limits -- nothing was sent:", file=sys.stderr)
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
        version = client.find_version(app.app_id, args.version)
        version_locs = client.list_localizations(version["id"])
        attributes = version["attributes"]
        version_state = attributes.get("appStoreState") or attributes.get("appVersionState")

        needs_app_info = any(
            FIELDS_BY_FILE[stem].level == APP_INFO for fields in local.texts.values() for stem in fields
        )
        app_info_id = None
        info_locs = None
        if needs_app_info:
            info = client.find_editable_app_info(app.app_id)
            if info is not None:
                app_info_id = info["id"]
                info_locs = client.list_app_info_localizations(app_info_id)

        plan = build_plan(local.texts, version_locs, info_locs)

        print(f"App: {app.key} (id {app.app_id})")
        print(f"Version: {args.version} (state: {version_state})")
        print(f"Source: {display_path(source)} ({len(local.texts)} locale(s))")
        if version_state in LIVE_VERSION_STATES:
            print("Note: this version is already live, so Apple will likely refuse changes to most fields.")
        print()

        if plan.problems:
            print("Cannot continue:", file=sys.stderr)
            for problem in plan.problems:
                print(f"  {problem}", file=sys.stderr)
            return 1

        if plan.changes:
            print(f"Changes ({len(plan.changes)}):")
            for change in plan.changes:
                suffix = "  [new locale]" if change.kind == "new locale" else ""
                print(f"  {change.locale:<8} {change.field.file:<17} {describe_change(change)}{suffix}")
        else:
            print("No changes: everything already matches App Store Connect.")
        print(f"Unchanged: {len(plan.unchanged)} field(s).")
        if plan.unavailable:
            names = sorted({spec.file for _, spec in plan.unavailable})
            print(f"Skipped: {', '.join(names)} -- no app info in an editable state "
                  "(one appears while a version is being prepared).")
        for note in local.notes:
            print(f"Note: {note}")
        print()

        if not plan.changes:
            return 0
        if args.dry_run:
            print("Dry run -- no changes made.")
            return 0
        if not confirm("Push these changes to App Store Connect now?", args.yes):
            return 1

        for locale in sorted({c.locale for c in plan.changes}):
            version_attrs = {
                c.field.attribute: c.new for c in plan.changes if c.locale == locale and c.field.level == VERSION
            }
            info_attrs = {
                c.field.attribute: c.new for c in plan.changes if c.locale == locale and c.field.level == APP_INFO
            }
            if version_attrs:
                if locale in version_locs:
                    client.update_localization(version_locs[locale]["id"], version_attrs)
                else:
                    client.create_localization(version["id"], locale, version_attrs)
            already = []
            if info_attrs:
                if locale not in info_locs:
                    # Creating a version localization for a new locale makes
                    # App Store Connect create the app info localization too,
                    # copying the primary language's name and subtitle. Look
                    # again before trying to create it ourselves, which would
                    # fail with a 409.
                    info_locs = client.list_app_info_localizations(app_info_id)
                if locale in info_locs:
                    current = info_locs[locale]["attributes"]
                    pending = {k: v for k, v in info_attrs.items() if normalize(current.get(k)) != normalize(v)}
                    already = sorted(c.field.file for c in plan.changes
                                     if c.locale == locale and c.field.level == APP_INFO
                                     and c.field.attribute not in pending)
                    if pending:
                        client.update_app_info_localization(info_locs[locale]["id"], pending)
                else:
                    client.create_app_info_localization(app_info_id, locale, info_attrs)
            written = sorted(c.field.file for c in plan.changes if c.locale == locale and c.field.file not in already)
            line = f"{locale}: wrote {', '.join(written)}" if written else f"{locale}: nothing left to write"
            if already:
                line += f" ({', '.join(already)} already matched)"
            print(line)
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        print("Locales listed above were already written; the rest were not.", file=sys.stderr)
        return 1

    print("\nDone. Review the listing in App Store Connect before submitting for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
