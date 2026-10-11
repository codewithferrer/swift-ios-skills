#!/usr/bin/env python3
"""Download an app's store listing text from App Store Connect into local
metadata files (see asc_metadata.py for the layout): name, subtitle, privacy
policy URL, description, keywords, promotional text, support URL and
marketing URL, one file per field per locale.

Use it to bootstrap the metadata folder for an app that so far only has its
listing in App Store Connect, and as a backup before running
push_metadata.py. It only reads from App Store Connect; it writes local
files only.

An existing local file whose content differs is left alone unless --force.

Usage:
    python pull_metadata.py --app my-app --version 1.9.2
    python pull_metadata.py --app my-app --version 1.9.2 --dry-run
    python pull_metadata.py --app my-app --version 1.9.2 --dir ./metadata --locales en-US,es-ES
    python pull_metadata.py --app my-app --version 1.9.2 --fields description,keywords --force
"""

from __future__ import annotations

import argparse
import sys

from asc_common import (
    ConfigError,
    add_app_arguments,
    add_metadata_arguments,
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
    REVIEW_FIELDS,
    REVIEW_GROUP,
    SECRET_REVIEW_FIELDS,
    normalize,
    remote_texts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    add_metadata_arguments(parser)
    parser.add_argument("--version", required=True, help="Exact marketing version string, e.g. 1.9.2")
    parser.add_argument("--force", action="store_true", help="Overwrite local files whose content differs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written, write nothing")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
        target = resolve_metadata_dir(args, app)
        only_locales = selected_locales(args)
        only_fields = selected_fields(args)
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()
        version = client.find_version(app.app_id, args.version)
        groups = set(GLOBAL_GROUPS)
        locale_fields = (only_fields - groups) if only_fields is not None else None
        want_locales = only_fields is None or bool(locale_fields)
        want_copyright = only_fields is None or COPYRIGHT_GROUP in only_fields
        want_review = only_fields is None or REVIEW_GROUP in only_fields

        version_locs = client.list_localizations(version["id"]) if want_locales else {}

        # Version-level extras. The demo account password is never pulled: it
        # would land in a plain text file, possibly inside a git repository.
        extras: dict[str, str] = {}
        if want_copyright and normalize(version["attributes"].get("copyright")):
            extras[f"{COPYRIGHT_GROUP}.txt"] = normalize(version["attributes"]["copyright"])
        if want_review:
            detail = client.get_review_detail(version["id"])
            for stem, attribute in REVIEW_FIELDS.items():
                value = normalize(((detail or {}).get("attributes") or {}).get(attribute))
                if value and stem not in SECRET_REVIEW_FIELDS:
                    extras[f"{REVIEW_GROUP}/{stem}.txt"] = value

        info_locs = None
        wants_app_info = want_locales and (only_fields is None or any(
            f in FIELDS_BY_FILE and FIELDS_BY_FILE[f].level == APP_INFO for f in locale_fields
        ))
        if wants_app_info:
            # Prefer the editable app info (it holds the pending name/subtitle)
            # and fall back to the live one, which is fine for reading.
            info = client.find_editable_app_info(app.app_id)
            if info is None:
                infos = client.list_app_infos(app.app_id)
                info = infos[0] if infos else None
            if info is not None:
                info_locs = client.list_app_info_localizations(info["id"])
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    remote = (remote_texts(version_locs, info_locs, only_locales, app.skip_locales, locale_fields)
              if want_locales else {})
    if not remote and not extras:
        print("Nothing to pull: App Store Connect returned no non-empty fields for this selection.", file=sys.stderr)
        return 1

    wanted_files = [(target / locale / f"{stem}.txt", text)
                    for locale in sorted(remote) for stem, text in remote[locale].items()]
    wanted_files += [(target / relative, text) for relative, text in sorted(extras.items())]

    new, unchanged, differs = [], [], []
    for path, text in wanted_files:
        if not path.exists():
            new.append((path, text))
        elif normalize(path.read_text(encoding="utf-8")) == text:
            unchanged.append(path)
        else:
            differs.append((path, text))

    print(f"App: {app.key} (id {app.app_id})")
    print(f"Version: {args.version}")
    print(f"Target: {display_path(target)}")
    found = f"Found {sum(len(v) for v in remote.values())} field(s) in {len(remote)} locale(s)"
    print(found + (f" and {len(extras)} version-level file(s)." if extras else "."))
    if want_review and any(s in SECRET_REVIEW_FIELDS for s in REVIEW_FIELDS):
        print("The demo account password is never pulled.")
    if info_locs is None and wants_app_info:
        print("No app info found, so name / subtitle / privacy_url are not included.")
    print()

    to_write = list(new) + (list(differs) if args.force else [])
    for path, _ in new:
        print(f"  new        {display_path(path)}")
    for path, _ in differs:
        action = "overwrite " if args.force else "differs   "
        print(f"  {action} {display_path(path)}" + ("" if args.force else "  (kept, use --force to overwrite)"))
    print(f"  unchanged  {len(unchanged)} file(s)")
    print()

    if args.dry_run:
        print(f"Dry run -- would write {len(to_write)} file(s), nothing written.")
        return 0

    for path, text in to_write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {len(to_write)} file(s).")
    if differs and not args.force:
        print(f"Kept {len(differs)} local file(s) that differ from App Store Connect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
