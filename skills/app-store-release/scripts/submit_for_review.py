#!/usr/bin/env python3
"""Submit an App Store version for review -- the App Store Connect API's
3-step flow (the old single-step appStoreVersionSubmissions resource was
retired by Apple): create a reviewSubmission container, attach the
appStoreVersion to it, then flip submitted=true to actually send it to
Apple's review queue.

Unlike the other scripts here, doing nothing is the DEFAULT: you must pass
either --prepare (steps 1+2 only, reversible, lets you inspect the draft
in App Store Connect first) or --submit (all 3 steps, irreversible -- this
is the actual "Add for Review" button) to make any real API call. Plain
--dry-run or no flag at all only prints what would happen.

Usage:
    python submit_for_review.py --app my-app --version 1.9.3               # dry-run by default
    python submit_for_review.py --app my-app --version 1.9.3 --prepare     # creates the draft, does NOT submit
    python submit_for_review.py --app my-app --version 1.9.3 --submit      # prepares AND submits -- irreversible
"""

from __future__ import annotations

import argparse
import sys

from asc_common import ConfigError, add_app_arguments, confirm_typed, load_client_module, resolve_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--version", required=True, help="versionString already created in App Store Connect")
    parser.add_argument("--prepare", action="store_true", help="Create the draft submission, do NOT submit it")
    parser.add_argument("--submit", action="store_true", help="Prepare AND submit for review -- irreversible")
    parser.add_argument("--yes", action="store_true", help="Skip the typed confirmation (--submit is still required to submit)")
    args = parser.parse_args()

    if args.prepare and args.submit:
        print("Pass either --prepare or --submit, not both.", file=sys.stderr)
        return 1

    try:
        app = resolve_app(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"App: {app.key} (id {app.app_id})")
    print(f"Version: {args.version}")
    print()

    if not args.prepare and not args.submit:
        print("Dry run (default) -- no API calls made. Steps this would perform:")
        print("  1. POST /reviewSubmissions                 (create a draft submission container)")
        print("  2. POST /reviewSubmissionItems              (attach this app version to it)")
        print("Pass --prepare to actually do steps 1-2 (reversible, inspectable in App Store Connect),")
        print("or --submit to do steps 1-2 and then also:")
        print("  3. PATCH /reviewSubmissions/{id} {submitted: true}   (sends it to Apple's review queue -- irreversible)")
        return 0

    try:
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.submit:
        print("This will submit the version to Apple's review queue. This cannot be undone from here --")
        print("cancelling afterward means withdrawing the submission by hand in App Store Connect.")
        if not confirm_typed(args.version, args.yes):
            return 1

    try:
        client = asc.AppStoreConnectClient()
        version = client.find_version(app.app_id, args.version)

        submission = client.create_review_submission(app.app_id)
        print(f"Created review submission (id {submission['id']}).")

        client.add_version_to_review_submission(submission["id"], version["id"])
        print(f"Attached version {args.version} to the submission.")

        if args.submit:
            client.submit_review_submission(submission["id"])
            print("\nSubmitted for review. Check status in App Store Connect or via:")
            print(f"  GET /reviewSubmissions/{submission['id']}")
        else:
            print("\nPrepared but NOT submitted -- nothing irreversible has happened.")
            print("Review the draft in App Store Connect, then rerun with --submit when ready")
            print(f"(reviewSubmission id: {submission['id']}).")

    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
