#!/usr/bin/env python3
"""Pre-submission check: what App Store Connect would still ask for before
this version can be sent to App Review. Read-only; it never changes anything.

It looks at the attached build and export compliance, the listing text and
screenshots per locale, copyright, privacy policy URL, category, age rating,
content rights, App Review contact details and the price schedule, plus
advisory items (subtitle, accessibility declarations, in-app purchase
states). Anything it cannot read is reported as "could not be checked" and
is never counted as a pass.

It cannot see everything: App Privacy answers and agreements/tax/banking
status are not exposed by the API, and the report says so.

Usage:
    python readiness.py --app my-app
    python readiness.py --app my-app --version 1.0.0
    python readiness.py --all
    python readiness.py --app my-app --strict     # exit 1 when something must be fixed
"""

from __future__ import annotations

import argparse
import re
import sys

from asc_common import ConfigError, add_app_arguments, load_all_apps, load_client_module, resolve_app
from asc_readiness import BLOCKING, evaluate, render
from asc_status import version_state


def short_error(exc: Exception) -> str:
    title = re.search(r'"title"\s*:\s*"([^"]+)"', str(exc))
    code = getattr(exc, "status_code", None)
    return (f"HTTP {code}" + (f" {title.group(1)}" if title else "")) if code else str(exc)[:100]


def gather(client, app, version_string: str | None) -> tuple[dict, dict]:
    """(snapshot for asc_readiness.evaluate, the version resource). A section
    that cannot be read is left out of the snapshot, which evaluate reports
    as "could not be checked".
    """
    versions = client.list_versions(app.app_id, limit=50)
    if not versions:
        raise ConfigError(f"{app.key} has no versions yet.")
    version = client.find_version(app.app_id, version_string) if version_string else versions[0]
    attrs = version["attributes"]
    snapshot: dict = {
        "version_string": attrs.get("versionString"),
        "first_release": len(versions) == 1,
        "copyright": attrs.get("copyright"),
        "errors": [],
    }

    def attempt(label, fn):
        try:
            return True, fn()
        except RuntimeError as exc:  # AppStoreConnectError subclasses RuntimeError
            snapshot["errors"].append(f"{label}: {short_error(exc)}")
            return False, None

    ok, build = attempt("build", lambda: client.get_version_build(version["id"]))
    if ok:
        snapshot["build"] = build["attributes"] if build else None
    ok, valid = attempt("latest build", lambda: client.list_builds(app.app_id, only_valid=True))
    if ok and valid:
        snapshot["latest_build"] = valid[0]["attributes"]

    ok, localizations = attempt("listing", lambda: client.list_localizations(version["id"]))
    if ok:
        snapshot["locales"] = {loc: res["attributes"] for loc, res in localizations.items()}

        def screenshot_counts():
            counts = {}
            for loc, res in localizations.items():
                per_type = {}
                for shot_set in client.list_screenshot_sets(res["id"]):
                    shots = client.list_screenshots(shot_set["id"])
                    per_type[shot_set["attributes"]["screenshotDisplayType"]] = len(shots)
                counts[loc] = per_type
            return counts

        ok, counts = attempt("screenshots", screenshot_counts)
        if ok:
            snapshot["screenshots"] = counts

    def app_info():
        info = client.find_editable_app_info(app.app_id)
        if info is None:
            infos = client.list_app_infos(app.app_id)
            info = infos[0] if infos else None
        if info is None:
            return {"available": False}
        locs = client.list_app_info_localizations(info["id"])
        return {
            "available": True,
            "privacy_urls": {loc: r["attributes"].get("privacyPolicyUrl") for loc, r in locs.items()},
            "subtitles": {loc: r["attributes"].get("subtitle") for loc, r in locs.items()},
            "primary_category": client.app_info_has(info["id"], "primaryCategory"),
            "age_rating": client.app_info_has(info["id"], "ageRatingDeclaration"),
        }

    ok, info = attempt("app info", app_info)
    if ok:
        snapshot["app_info"] = info

    ok, app_resource = attempt("app", lambda: client.get_app(app.app_id))
    if ok:
        snapshot["content_rights"] = app_resource["attributes"].get("contentRightsDeclaration")
        snapshot["primary_locale"] = app_resource["attributes"].get("primaryLocale")

    ok, detail = attempt("App Review information", lambda: client.get_review_detail(version["id"]))
    if ok:
        snapshot["review_detail"] = detail["attributes"] if detail else None

    ok, schedule = attempt("price schedule", lambda: client.get_price_schedule(app.app_id))
    if ok:
        snapshot["price_schedule"] = schedule is not None

    ok, declarations = attempt("accessibility", lambda: client.list_accessibility_declarations(app.app_id))
    if ok:
        snapshot["accessibility"] = [d["attributes"] for d in declarations]

    def purchases():
        items = []
        for iap in client.list_in_app_purchases(app.app_id):
            a = iap["attributes"]
            items.append({"name": a.get("productId") or a.get("name"), "state": a.get("state")})
        for sub in client.list_subscriptions(app.app_id):
            a = sub["attributes"]
            items.append({"name": a.get("productId") or a.get("name"), "state": a.get("state")})
        return items

    ok, items = attempt("in-app purchases", purchases)
    if ok:
        snapshot["purchases"] = items
    return snapshot, version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--all", action="store_true", help="Check every app in the apps file")
    parser.add_argument("--version", help="Version to check (default: the newest)")
    parser.add_argument("--strict", action="store_true", help="Exit with status 1 when anything must be fixed")
    args = parser.parse_args()

    try:
        apps = load_all_apps(args.apps_file) if args.all else [resolve_app(args)]
        asc = load_client_module()
        client = asc.AppStoreConnectClient()
        reports, failing = [], 0
        for app in apps:
            snapshot, version = gather(client, app, args.version)
            checks = evaluate(snapshot)
            failing += sum(1 for c in checks if c.severity == BLOCKING and c.ok is False)
            text = render(app.key, snapshot["version_string"], version_state(version["attributes"]),
                          snapshot["first_release"], checks)
            if snapshot["errors"]:
                text += "\n\nSections that failed to load: " + "; ".join(snapshot["errors"])
            reports.append(text)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1

    print("\n\n".join(reports))
    return 1 if (args.strict and failing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
