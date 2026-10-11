#!/usr/bin/env python3
"""Push an app's store listing text from local metadata files (see
asc_metadata.py for the layout) to App Store Connect: name, subtitle,
privacy policy URL, description, keywords, promotional text, support URL and
marketing URL, for every locale folder found. Also the version's copyright
line (`copyright.txt`) and its App Review information (the files in
`review_information/`: contact name, phone, email, demo account, notes).

It compares each field with what App Store Connect holds and only writes the
ones that differ. Text that is certainly over Apple's character limits is
rejected before any API call. "What's New" is not handled here; see
push_whats_new.py.

Per-version fields (description, keywords, promotional text, URLs, copyright,
review information) need the version to exist and be editable. App-level
fields (name, subtitle, privacy URL) need an app info in an editable state,
which Apple exposes while a version is being prepared; when there is none they
are reported and skipped, and the per-version fields are still pushed.

The demo account password is never printed. It lives in a plain text file, so
the script warns when that file sits in a git repository without being
ignored.

Run pull_metadata.py first if you want a backup of what is live.

Usage:
    python push_metadata.py --app my-app --version 1.9.3 --dry-run
    python push_metadata.py --app my-app --version 1.9.3
    python push_metadata.py --app my-app --version 1.9.3 --fields promotional_text --locales en-US
    python push_metadata.py --app my-app --version 1.0.0 --fields copyright,review_information
    python push_metadata.py --app my-app --version 1.9.3 --yes   # skip the confirmation prompt
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

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
    COPYRIGHT_GROUP,
    FIELDS_BY_FILE,
    GLOBAL_GROUPS,
    REVIEW_GROUP,
    VERSION,
    LocalMetadata,
    build_plan,
    check_limits,
    describe_change,
    describe_global,
    normalize,
    plan_globals,
    read_global,
    read_local,
    review_attributes,
)

LIVE_VERSION_STATES = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION"}


def tracked_secret_warning(path: Path) -> str | None:
    """A warning when `path` is inside a git repository and not ignored."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path.parent), "check-ignore", "-q", str(path)], capture_output=True
        )
    except OSError:
        return None
    if result.returncode == 1:  # inside a repository, and not ignored
        return (f"{display_path(path)} holds a password and is not ignored by git, so a commit would "
                "include it. Add it to .gitignore.")
    return None


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

    # --fields may name locale fields, the two version-level groups, or both.
    groups = set(GLOBAL_GROUPS)
    locale_fields = (only_fields - groups) if only_fields is not None else None
    want_locales = only_fields is None or bool(locale_fields)
    want_copyright = only_fields is None or COPYRIGHT_GROUP in only_fields
    want_review = only_fields is None or REVIEW_GROUP in only_fields

    if not source.is_dir():
        print(f"{source} is not a directory -- see references/metadata-files.md for the layout.", file=sys.stderr)
        return 1
    local = read_local(source, only_locales, app.skip_locales, locale_fields) if want_locales else LocalMetadata()
    extras = read_global(source)
    copyright_text = extras.copyright if want_copyright else None
    review = extras.review if want_review else {}
    review_attrs, review_problems, review_warnings = review_attributes(review)

    if not local.texts and copyright_text is None and not review_attrs:
        print(f"No metadata files found in {source} for this selection.", file=sys.stderr)
        for note in local.notes + extras.notes:
            print(f"  {note}", file=sys.stderr)
        return 1

    problems = check_limits(local.texts) + review_problems
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
        version = client.find_version(app.app_id, args.version)
        version_locs = client.list_localizations(version["id"]) if local.texts else {}
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

        review_detail = client.get_review_detail(version["id"]) if review_attrs else None
        global_changes, global_unchanged = plan_globals(
            copyright_text,
            attributes.get("copyright"),
            review_attrs,
            review_detail["attributes"] if review_detail else None,
        )

        print(f"App: {app.key} (id {app.app_id})")
        print(f"Version: {args.version} (state: {version_state})")
        parts = []
        if local.texts:
            parts.append(f"{len(local.texts)} locale(s)")
        if copyright_text is not None:
            parts.append("copyright")
        if review_attrs:
            parts.append("App Review information")
        print(f"Source: {display_path(source)} ({', '.join(parts)})")
        if version_state in LIVE_VERSION_STATES:
            print("Note: this version is already live, so Apple will likely refuse changes to most fields.")
        print()

        if plan.problems:
            print("Cannot continue:", file=sys.stderr)
            for problem in plan.problems:
                print(f"  {problem}", file=sys.stderr)
            return 1

        nothing_to_do = not plan.changes and not global_changes
        if plan.changes:
            print(f"Changes ({len(plan.changes)}):")
            for change in plan.changes:
                suffix = "  [new locale]" if change.kind == "new locale" else ""
                print(f"  {change.locale:<8} {change.field.file:<17} {describe_change(change)}{suffix}")
        if global_changes:
            print(f"Version info and App Review information ({len(global_changes)}):")
            for change in global_changes:
                where = "copyright" if change.group == COPYRIGHT_GROUP else f"review: {change.label}"
                print(f"  {where:<26} {describe_global(change)}")
        if nothing_to_do:
            print("No changes: everything already matches App Store Connect.")
        print(f"Unchanged: {len(plan.unchanged) + global_unchanged} field(s).")
        if plan.unavailable:
            names = sorted({spec.file for _, spec in plan.unavailable})
            print(f"Skipped: {', '.join(names)} -- no app info in an editable state "
                  "(one appears while a version is being prepared).")
        for note in local.notes + extras.notes:
            print(f"Note: {note}")
        for warning in review_warnings:
            print(f"Warning: {warning}")
        if "demo_password" in review and "demo_password" in extras.paths:
            secret_warning = tracked_secret_warning(extras.paths["demo_password"])
            if secret_warning:
                print(f"Warning: {secret_warning}")
        print()

        if nothing_to_do:
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

        copyright_change = [c for c in global_changes if c.group == COPYRIGHT_GROUP]
        if copyright_change:
            client.update_version(version["id"], {"copyright": copyright_change[0].new})
            print("Version info: wrote copyright")
        review_pending = {c.attribute: c.new for c in global_changes if c.group == REVIEW_GROUP}
        if review_pending:
            if review_detail:
                client.update_review_detail(review_detail["id"], review_pending)
                print(f"App Review information: updated {len(review_pending)} field(s)")
            else:
                client.create_review_detail(version["id"], review_attrs)
                print(f"App Review information: created with {len(review_attrs)} field(s)")
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        print("Anything listed above was already written; the rest was not.", file=sys.stderr)
        return 1

    print("\nDone. Review the listing in App Store Connect before submitting for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
