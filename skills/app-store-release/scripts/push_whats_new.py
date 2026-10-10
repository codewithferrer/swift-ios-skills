#!/usr/bin/env python3
"""Push a RELEASE_NOTES.md's "What's New" text to App Store Connect for
every locale it covers, for one app and one version, in one run.

Usage:
    python push_whats_new.py --app my-app --version 1.9.2
    python push_whats_new.py --app my-app --version 1.9.2 --dry-run
    python push_whats_new.py --app my-app --version 1.9.2 --yes   # skip the confirmation prompt

The notes file is the app's `release_notes_path` in the apps file. See
references/setup.md for one-time setup (API key, apps file) and
references/usage.md for the notes format.
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
)
from release_notes import parse as parse_release_notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--version", required=True, help="Exact marketing version string, e.g. 1.9.2")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print, make no API calls")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    if app.release_notes_path is None:
        print(f"App '{app.key}' has no \"release_notes_path\" in {app.apps_file}.", file=sys.stderr)
        return 1

    try:
        locale_texts = parse_release_notes(app.release_notes_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not locale_texts:
        print(
            f"No locale blocks found in {app.release_notes_path}. Each locale needs a heading like "
            "'## en-US (English)' followed by a fenced code block.",
            file=sys.stderr,
        )
        return 1

    # Locale codes that appear in the notes for other channels (blog posts,
    # in-app "what's new" screens...) but aren't valid App Store Connect
    # metadata locales would just fail the API call, so the app's
    # `skip_locales` setting drops them with a note instead.
    pushable = {locale: text for locale, text in locale_texts.items() if locale not in app.skip_locales}
    skipped = sorted(set(locale_texts) & app.skip_locales)

    print(f"App: {app.key} (id {app.app_id})")
    print(f"Version: {args.version}")
    print(f"Source: {display_path(app.release_notes_path)}")
    print(f"Locales to push ({len(pushable)}): {', '.join(sorted(pushable))}")
    if skipped:
        print(f"Skipped (listed in skip_locales): {', '.join(skipped)}")
    print()

    if args.dry_run:
        for locale, text in sorted(pushable.items()):
            print(f"--- {locale} ---")
            print(text)
            print()
        print("Dry run -- no API calls made.")
        return 0

    if not confirm("Push this to App Store Connect now?", args.yes):
        return 1

    try:
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()
        version = client.find_version(app.app_id, args.version)
        existing = client.list_localizations(version["id"])

        for locale, text in sorted(pushable.items()):
            attributes = {"whatsNew": text}
            if locale in existing:
                client.update_localization(existing[locale]["id"], attributes)
                print(f"Updated {locale}")
            else:
                client.create_localization(version["id"], locale, attributes)
                print(f"Created {locale} (was missing on this version)")
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    print("\nDone. Review the draft in App Store Connect before submitting for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
