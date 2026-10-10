#!/usr/bin/env python3
"""Where an app stands, in one read-only report: its newest versions and
their review/release state, the latest processed build, any upload since then
that failed or is stuck (with Apple's error codes), the latest Xcode Cloud
run, and how many written customer reviews are still waiting for a reply.

Each section is fetched independently: one that the API key cannot read is
listed as "not available" instead of failing the whole report.

Usage:
    python status.py --app my-app
    python status.py --all
    python status.py --app my-app --versions 5
"""

from __future__ import annotations

import argparse
import re
import sys

from asc_common import ConfigError, add_app_arguments, load_all_apps, load_client_module, resolve_app
from asc_status import parse_date, render_status, unfinished_uploads


def short_error(exc: Exception) -> str:
    title = re.search(r'"title"\s*:\s*"([^"]+)"', str(exc))
    code = getattr(exc, "status_code", None)
    return f"HTTP {code}" + (f" {title.group(1)}" if title else "") if code else str(exc)[:120]


def gather(client, app_id: str, versions: int) -> dict:
    data: dict = {"notes": []}

    def attempt(label, fetch):
        try:
            return True, fetch()
        except RuntimeError as exc:  # AppStoreConnectError subclasses RuntimeError
            data["notes"].append(f"{label}: {short_error(exc)}")
            return False, None

    ok, found = attempt("versions", lambda: client.list_versions(app_id, versions))
    if ok:
        data["versions"] = found

    latest_valid_date = None
    ok, builds = attempt("builds", lambda: client.list_builds(app_id, only_valid=False))
    if ok and builds:
        data["build"] = builds[0]
        valid = next((b for b in builds if b["attributes"].get("processingState") == "VALID"), None)
        if valid:
            latest_valid_date = parse_date(valid["attributes"].get("uploadedDate"))

    ok, uploads = attempt("build uploads", lambda: client.list_build_uploads(app_id, limit=15))
    if ok:
        data["uploads"] = uploads
        data["problem_uploads"] = unfinished_uploads(uploads, latest_valid_date)

    ok, runs = attempt("Xcode Cloud", lambda: client.list_ci_build_runs(app_id, limit=1))
    if ok:
        data["ci_runs"] = runs

    ok, reviews = attempt("customer reviews", lambda: client.list_customer_reviews(app_id, limit=50, unanswered=True))
    if ok:
        data["reviews"] = {"unanswered": len(reviews[0])}
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--all", action="store_true", help="Report on every app in the apps file")
    parser.add_argument("--versions", type=int, default=3, help="How many recent versions to list (default: 3)")
    args = parser.parse_args()

    try:
        apps = load_all_apps(args.apps_file) if args.all else [resolve_app(args)]
        asc = load_client_module()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        client = asc.AppStoreConnectClient()
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    reports = [render_status(app.key, app.app_id, gather(client, app.app_id, args.versions)) for app in apps]
    print("\n\n".join(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
