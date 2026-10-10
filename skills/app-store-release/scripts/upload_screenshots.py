#!/usr/bin/env python3
"""Upload a folder of PNG screenshots to App Store Connect for one app,
version, locale, and display type -- reserve, PUT the bytes to Apple's
pre-signed URLs, commit, then set the display order to match the files'
sort order.

Usage:
    python upload_screenshots.py --app my-app --version 1.9.2 --locale en-US --dir ./final
    python upload_screenshots.py ... --dry-run
    python upload_screenshots.py ... --replace   # delete the set's existing screenshots first
    python upload_screenshots.py ... --yes       # skip the confirmation prompt

Files are uploaded in sorted filename order, which also becomes the display
order in App Store Connect (name them 01_..., 02_... etc.).

The display type comes from --display-type, else the app's
"screenshot_display_type" in the apps file, else APP_IPHONE_65. Use the exact
value of Apple's ScreenshotDisplayType enum (APP_IPHONE_67, APP_IPHONE_69,
APP_IPAD_PRO_3GEN_129, ...). The sizes Apple requires change over time, so
check App Store Connect's screenshot specifications for the current ones.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from asc_common import ConfigError, add_app_arguments, confirm, load_client_module, resolve_app

DEFAULT_DISPLAY_TYPE = "APP_IPHONE_65"  # 6.5" iPhone


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--version", required=True, help="versionString already created in App Store Connect")
    parser.add_argument("--locale", required=True, help="e.g. en-US")
    parser.add_argument("--dir", required=True, help="Folder of .png screenshots, uploaded in sorted filename order")
    parser.add_argument("--display-type", help=f"ScreenshotDisplayType enum value (default: {DEFAULT_DISPLAY_TYPE})")
    parser.add_argument("--replace", action="store_true", help="Delete the set's existing screenshots first")
    parser.add_argument("--dry-run", action="store_true", help="Look things up and print, make no changes")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    display_type = args.display_type or app.screenshot_display_type or DEFAULT_DISPLAY_TYPE

    shots_dir = Path(args.dir)
    files = sorted(shots_dir.glob("*.png"))
    if not files:
        print(f"No .png files found in {shots_dir}", file=sys.stderr)
        return 1

    print(f"App: {app.key} (id {app.app_id})")
    print(f"Version: {args.version}")
    print(f"Locale: {args.locale}")
    print(f"Display type: {display_type}")
    print(f"Files ({len(files)}), in upload/display order:")
    for f in files:
        print(f"  {f.name} ({f.stat().st_size:,} bytes)")
    print()

    try:
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()
        version = client.find_version(app.app_id, args.version)
        localizations = client.list_localizations(version["id"])
        if args.locale not in localizations:
            print(f"No '{args.locale}' localization on version {args.version}. "
                  f"Available: {', '.join(sorted(localizations))}", file=sys.stderr)
            return 1
        localization_id = localizations[args.locale]["id"]

        sets = client.list_screenshot_sets(localization_id)
        existing_set = next(
            (s for s in sets if s["attributes"]["screenshotDisplayType"] == display_type), None
        )

        existing_shots = []
        if existing_set:
            existing_shots = client.list_screenshots(existing_set["id"])
            print(f"Existing screenshot set found (id {existing_set['id']}) with {len(existing_shots)} screenshot(s).")
            if existing_shots and not args.replace:
                print("Pass --replace to delete these first, or they'll stay alongside the new ones.", file=sys.stderr)
                return 1
        else:
            print("No screenshot set yet for this display type -- will create one.")

        if args.dry_run:
            if existing_shots and args.replace:
                print(f"Would delete {len(existing_shots)} existing screenshot(s).")
            print(f"Would upload {len(files)} file(s) and set that display order.")
            print("Dry run -- no changes made.")
            return 0

        if not confirm("Proceed on App Store Connect now?", args.yes):
            return 1

        if not existing_set:
            existing_set = client.create_screenshot_set(localization_id, display_type)
            print(f"Created screenshot set (id {existing_set['id']}).")

        if existing_shots and args.replace:
            for shot in existing_shots:
                client.delete_screenshot(shot["id"])
            print(f"Deleted {len(existing_shots)} existing screenshot(s).")

        uploaded_ids = []
        for f in files:
            file_bytes = f.read_bytes()
            reservation = client.reserve_screenshot_upload(existing_set["id"], f.name, len(file_bytes))
            client.upload_screenshot_bytes(reservation["attributes"]["uploadOperations"], file_bytes)
            checksum = md5_of(f)
            client.commit_screenshot_upload(reservation["id"], checksum)
            uploaded_ids.append(reservation["id"])
            print(f"Uploaded {f.name} (id {reservation['id']})")

        print("\nWaiting for Apple to process the uploads...")
        for shot_id, f in zip(uploaded_ids, files):
            state = "AWAITING_UPLOAD"
            for _ in range(30):
                shot = client.get_screenshot(shot_id)
                state = shot["attributes"].get("assetDeliveryState", {}).get("state", state)
                if state in ("COMPLETE", "FAILED"):
                    break
                time.sleep(2)
            marker = "OK" if state == "COMPLETE" else state
            print(f"  {f.name}: {marker}")

        client.order_screenshots(existing_set["id"], uploaded_ids)
        print("\nDone. Display order set to match the uploaded file order.")
        print("Review the set in App Store Connect before submitting for review.")

    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
