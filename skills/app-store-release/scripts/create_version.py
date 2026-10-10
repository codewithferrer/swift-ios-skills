#!/usr/bin/env python3
"""Create a new App Store version (if it doesn't already exist) and attach
an already-uploaded, processed build to it -- the two steps that normally
mean opening App Store Connect and clicking "+ Version" then picking a
build from a dropdown.

Does NOT upload a binary -- that still happens via Xcode/Transporter/altool,
same as always. This only wires up what's already sitting in App Store
Connect after that upload finishes processing.

Usage:
    python create_version.py --app my-app --version 1.9.3 --build 26
    python create_version.py --app my-app --version 1.9.3 --build 26 --dry-run
    python create_version.py --app my-app --version 1.9.3 --build 26 --yes

    # No --build: lists the most recent processed builds so you can pick one
    python create_version.py --app my-app --version 1.9.3
"""

from __future__ import annotations

import argparse
import sys

from asc_common import ConfigError, add_app_arguments, confirm, load_client_module, resolve_app

RELEASE_TYPES = {
    "manual": "MANUAL",
    "after_approval": "AFTER_APPROVAL",
    "scheduled": "SCHEDULED",
}


def describe_build(build: dict) -> str:
    attrs = build["attributes"]
    return f"{attrs.get('version')} (uploaded {attrs.get('uploadedDate', '?')[:10]}, id {build['id']})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--version", required=True, help="Marketing version string to create, e.g. 1.9.3")
    parser.add_argument("--build", help="Exact build number (CFBundleVersion) to attach, e.g. 26")
    parser.add_argument(
        "--release-type",
        choices=RELEASE_TYPES.keys(),
        default="manual",
        help="manual (default, needs an explicit release step later) / after_approval / scheduled",
    )
    parser.add_argument("--dry-run", action="store_true", help="Look things up and print, make no changes")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()

        existing = client.find_version_or_none(app.app_id, args.version)
        if existing:
            print(f"Version {args.version} already exists (id {existing['id']}, state "
                  f"{existing['attributes'].get('appVersionState')}) -- will attach the build to it "
                  "instead of creating a new one.")
        else:
            print(f"Version {args.version} does not exist yet -- will create it "
                  f"(platform IOS, releaseType {RELEASE_TYPES[args.release_type]}).")

        builds = client.list_builds(app.app_id, build_number=args.build)
        if not builds:
            if args.build:
                print(f"No processed build '{args.build}' found for this app yet -- "
                      "upload it via Xcode/Transporter and wait for processing to finish.", file=sys.stderr)
            else:
                print("No processed builds found for this app at all yet.", file=sys.stderr)
            return 1

        if not args.build and len(builds) > 1:
            print("\nMultiple processed builds found, newest first -- rerun with --build <number>:")
            for build in builds:
                print(f"  {describe_build(build)}")
            return 1

        target_build = builds[0]
        print(f"Build to attach: {describe_build(target_build)}")
        print()

        if args.dry_run:
            print("Dry run -- no changes made.")
            return 0

        if not confirm("Proceed on App Store Connect now?", args.yes):
            return 1

        if existing:
            version = existing
        else:
            version = client.create_version(
                app.app_id, args.version, release_type=RELEASE_TYPES[args.release_type]
            )
            print(f"Created version {args.version} (id {version['id']}).")

        client.assign_build(version["id"], target_build["id"])
        print(f"Attached build {target_build['attributes'].get('version')} to version {args.version}.")

    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    print("\nDone. Fill in the What's New text (push_whats_new.py) and review the rest in App Store Connect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
