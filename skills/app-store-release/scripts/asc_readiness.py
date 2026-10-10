"""Pre-submission readiness: given a snapshot of what App Store Connect holds
for one version, say what Apple would still ask for. Pure logic -- no
network, no third-party imports -- so every rule can be unit tested.

Each check has three outcomes, not two: it passes, it fails, or it could not
be read (`ok is None`). The last one is never reported as a pass: a report
that says "ready" because a request failed would be worse than no report.

This is a convenience, not a guarantee. It reflects the checks that the API
makes visible; see NOT_VISIBLE for what it cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass

from asc_metadata import normalize

BLOCKING = "blocking"
ADVISORY = "advisory"

# Things App Store Connect requires or checks that the API does not expose.
NOT_VISIBLE = (
    "App Privacy answers (the privacy nutrition labels)",
    "agreements, tax and banking status",
    "whether the app complies with App Review Guidelines",
)

PROBLEM_PURCHASE_STATES = {"MISSING_METADATA", "DEVELOPER_ACTION_NEEDED", "REJECTED"}


@dataclass
class Check:
    severity: str
    label: str
    ok: bool | None  # None: could not be read
    detail: str = ""


def _blank(value) -> bool:
    return not normalize(value)


def _missing(per_locale: dict, locales) -> list[str]:
    return sorted(loc for loc in locales if _blank(per_locale.get(loc)))


def _listing_field(s: dict, attribute: str, label: str, severity: str = BLOCKING) -> Check:
    locales = s.get("locales")
    if locales is None:
        return Check(severity, label, None, "the listing could not be read")
    gaps = _missing({loc: v.get(attribute) for loc, v in locales.items()}, locales)
    if gaps:
        return Check(severity, label, False, f"missing in {', '.join(gaps)}")
    return Check(severity, label, True, f"{len(locales)} locale(s)")


def evaluate(s: dict) -> list[Check]:
    checks: list[Check] = []
    add = checks.append

    # -- Build -----------------------------------------------------------------
    build, latest = s.get("build"), s.get("latest_build")
    if "build" not in s:
        add(Check(BLOCKING, "Build attached", None, "could not be read"))
    elif build is None:
        hint = (f"; the latest processed build is {latest['version']} -- attach it with "
                f"create_version.py --version {s.get('version_string', 'V')} --build {latest['version']}"
                if latest else "; no processed build exists yet")
        add(Check(BLOCKING, "Build attached", False, "no build is attached to this version" + hint))
    elif build.get("processingState") != "VALID":
        add(Check(BLOCKING, "Build attached", False,
                  f"build {build.get('version')} is {build.get('processingState')}, not VALID"))
    else:
        add(Check(BLOCKING, "Build attached", True, f"build {build.get('version')}"))
        encryption = build.get("usesNonExemptEncryption")
        if encryption is None:
            add(Check(BLOCKING, "Export compliance", False,
                      f"not answered for build {build.get('version')} (set ITSAppUsesNonExemptEncryption "
                      "in Info.plist, or answer it in App Store Connect)"))
        else:
            add(Check(BLOCKING, "Export compliance", True,
                      "declares non-exempt encryption" if encryption else "declares no non-exempt encryption"))

    # -- Listing -----------------------------------------------------------------
    add(_listing_field(s, "description", "Description"))
    add(_listing_field(s, "keywords", "Keywords"))
    add(_listing_field(s, "supportUrl", "Support URL"))
    if not s.get("first_release", False):
        add(_listing_field(s, "whatsNew", "What's New"))

    # Only the primary locale needs screenshots of its own: a locale with none
    # shows the primary locale's. Counting every locale as missing would flag
    # apps that are already live.
    shots, locales, primary = s.get("screenshots"), s.get("locales"), s.get("primary_locale")
    if shots is None or locales is None:
        add(Check(BLOCKING, "Screenshots", None, "could not be read"))
    else:
        def total(loc: str) -> int:
            return sum(shots.get(loc, {}).values())

        def describe(loc: str) -> str:
            return ", ".join(f"{t}={n}" for t, n in sorted(shots.get(loc, {}).items()) if n)

        if primary in locales:
            if total(primary) == 0:
                add(Check(BLOCKING, "Screenshots", False,
                          f"none uploaded for the primary locale {primary} (other locales fall back to it)"))
            else:
                own = sorted(loc for loc in locales if loc != primary and total(loc))
                tail = f"; also own sets in {', '.join(own)}" if own else "; other locales use these"
                add(Check(BLOCKING, "Screenshots", True, f"{primary}: {describe(primary)}{tail}"))
        elif not any(total(loc) for loc in locales):
            add(Check(BLOCKING, "Screenshots", False, "none uploaded in any locale"))
        else:
            add(Check(BLOCKING, "Screenshots", True, ""))

    copyright_text = s.get("copyright")
    if "copyright" not in s:
        add(Check(BLOCKING, "Copyright", None, "could not be read"))
    else:
        add(Check(BLOCKING, "Copyright", not _blank(copyright_text),
                  "" if not _blank(copyright_text) else "empty"))

    # -- App information ---------------------------------------------------------
    info = s.get("app_info")
    if info is None:
        for label in ("Privacy policy URL", "Primary category", "Age rating"):
            add(Check(BLOCKING, label, None, "could not be read"))
    else:
        if not info.get("available", True):
            add(Check(BLOCKING, "Privacy policy URL", None, "no editable app info to read it from"))
        else:
            urls = info.get("privacy_urls", {})
            gaps = _missing(urls, urls)
            add(Check(BLOCKING, "Privacy policy URL", not gaps, f"missing in {', '.join(gaps)}" if gaps else ""))
        add(Check(BLOCKING, "Primary category", bool(info.get("primary_category")),
                  "" if info.get("primary_category") else "not set"))
        add(Check(BLOCKING, "Age rating", bool(info.get("age_rating")),
                  "" if info.get("age_rating") else "not answered"))

    rights = s.get("content_rights")
    if "content_rights" not in s:
        add(Check(BLOCKING, "Content rights declaration", None, "could not be read"))
    else:
        add(Check(BLOCKING, "Content rights declaration", not _blank(rights), "" if rights else "not declared"))

    # -- App Review information ---------------------------------------------------
    if "review_detail" not in s:
        add(Check(BLOCKING, "App Review contact", None, "could not be read"))
    else:
        detail = s["review_detail"]
        if detail is None:
            add(Check(BLOCKING, "App Review contact", False, "App Review information has not been created"))
        else:
            required = {
                "first name": "contactFirstName", "last name": "contactLastName",
                "email": "contactEmail", "phone": "contactPhone",
            }
            gaps = [name for name, key in required.items() if _blank(detail.get(key))]
            if detail.get("demoAccountRequired"):
                gaps += [name for name, key in (("demo account name", "demoAccountName"),
                                                ("demo account password", "demoAccountPassword"))
                         if _blank(detail.get(key))]
            add(Check(BLOCKING, "App Review contact", not gaps, f"missing: {', '.join(gaps)}" if gaps else ""))

    # -- Pricing -------------------------------------------------------------------
    if "price_schedule" not in s:
        add(Check(BLOCKING, "Price schedule", None, "could not be read"))
    else:
        add(Check(BLOCKING, "Price schedule", bool(s["price_schedule"]),
                  "" if s["price_schedule"] else "no price has been set (a free app needs the free tier set)"))

    # -- Advisory ----------------------------------------------------------------------
    if info is not None and info.get("available", True):
        subtitles = info.get("subtitles", {})
        gaps = _missing(subtitles, subtitles)
        add(Check(ADVISORY, "Subtitle", not gaps, f"empty in {', '.join(gaps)}" if gaps else ""))

    declarations = s.get("accessibility")
    if declarations:
        drafts = [d for d in declarations if d.get("state") != "PUBLISHED"]
        add(Check(ADVISORY, "Accessibility declarations", not drafts,
                  f"{len(drafts)} of {len(declarations)} not published (state {drafts[0].get('state')})" if drafts else ""))

    purchases = s.get("purchases")
    if purchases:
        stuck = [p for p in purchases if p.get("state") in PROBLEM_PURCHASE_STATES]
        shown = ", ".join(f"{p['name']}: {p['state']}" for p in (stuck or purchases)[:6])
        add(Check(ADVISORY, "In-app purchases and subscriptions", not stuck, shown))
    return checks


def verdict(checks: list[Check]) -> str:
    failing = sum(1 for c in checks if c.severity == BLOCKING and c.ok is False)
    unknown = sum(1 for c in checks if c.severity == BLOCKING and c.ok is None)
    if failing:
        return f"NOT READY: {failing} item(s) to fix" + (f", {unknown} could not be checked" if unknown else "")
    if unknown:
        return f"UNCLEAR: nothing failing, but {unknown} item(s) could not be checked"
    return "READY as far as the API can tell (see what it cannot see below)"


def render(app_key: str, version_string: str, state: str | None, first_release: bool, checks: list[Check]) -> str:
    out = [f"{app_key}  {version_string}  ({state})" + ("  first release" if first_release else "")]

    def section(title: str, items: list[Check], mark: str):
        if not items:
            return
        out.append("")
        out.append(f"{title} ({len(items)})")
        for check in items:
            out.append(f"  [{mark}] {check.label:<30} {check.detail}".rstrip())

    blocking = [c for c in checks if c.severity == BLOCKING]
    section("Needs fixing before submitting", [c for c in blocking if c.ok is False], " ")
    section("Could not be checked", [c for c in checks if c.ok is None], "?")
    advisory_fail = [c for c in checks if c.severity == ADVISORY and c.ok is False]
    section("Worth a look, not required", advisory_fail, "~")
    section("Passed", [c for c in checks if c.ok is True], "x")

    out.append("")
    out.append("Not visible through the API, check these in App Store Connect:")
    out.extend(f"  - {item}" for item in NOT_VISIBLE)
    out.append("")
    out.append(verdict(checks))
    return "\n".join(out)
