#!/usr/bin/env python3
"""Release an Apple-approved version -- the "Release This Version" button,
for versions created with --release-type manual (the default in
create_version.py). Only relevant once the version is waiting on a manual
release (PENDING_DEVELOPER_RELEASE); this script checks that before doing
anything.

There is no API-level undo for this call (Apple's appStoreVersionReleaseRequests
resource is POST-only -- no GET/PATCH/DELETE), so it needs the same typed
confirmation as submit_for_review.py's --submit.

Usage:
    python release_version.py --app my-app --version 1.9.3               # dry-run by default
    python release_version.py --app my-app --version 1.9.3 --release     # releases now -- irreversible
"""

from __future__ import annotations

import argparse
import sys

from asc_common import ConfigError, add_app_arguments, confirm_typed, load_client_module, resolve_app

WAITING_STATE = "PENDING_DEVELOPER_RELEASE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--version", required=True, help="versionString already approved by Apple")
    parser.add_argument("--release", action="store_true", help="Actually release the version now -- irreversible")
    parser.add_argument("--yes", action="store_true", help="Skip the typed confirmation (--release is still required to release)")
    args = parser.parse_args()

    try:
        app = resolve_app(args)
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()
        version = client.find_version(app.app_id, args.version)
        attributes = version["attributes"]
        # appStoreState is the long-standing attribute this check was written
        # against. appVersionState is its newer sibling and the two use
        # different values for the same moment (READY_FOR_SALE vs
        # READY_FOR_DISTRIBUTION), so it is only a fallback in case Apple stops
        # returning appStoreState. If the value is not the one we expect, the
        # script reports "nothing to do" instead of releasing.
        state = attributes.get("appStoreState") or attributes.get("appVersionState")

        print(f"App: {app.key} (id {app.app_id})")
        print(f"Version: {args.version}")
        print(f"Current state: {state}")
        print()

        if state != WAITING_STATE:
            print(
                f"This version is not waiting on a manual release (state is {state}, "
                f"not {WAITING_STATE}) -- nothing to do.",
                file=sys.stderr,
            )
            return 1

        if not args.release:
            print("Dry run (default) -- no API call made.")
            print("Pass --release to actually publish this version to the App Store now.")
            return 0

        print("This will make the version publicly available on the App Store. There is no API")
        print("undo for this -- once released, pulling it back means removing the app from sale by hand.")
        if not confirm_typed(args.version, args.yes):
            return 1

        client.release_version(version["id"])
        print("\nReleased. The version will show as READY_FOR_SALE once it finishes propagating.")

    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
