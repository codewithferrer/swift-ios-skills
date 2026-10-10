"""Turning raw API data into the status report and the localization coverage
report. Pure functions only -- no network, no third-party imports -- so the
formatting and the "is this upload a problem?" judgement can be unit tested.

The scripts that fetch the data are status.py and audit_localizations.py;
create_version.py reuses `explain_missing_build` to say why a build is not
there yet instead of just "not found".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from asc_metadata import normalize

# What each version state means in practice. States not listed are shown as is.
STATE_HINTS = {
    "PREPARE_FOR_SUBMISSION": "editable draft",
    "READY_FOR_REVIEW": "ready to submit",
    "WAITING_FOR_REVIEW": "in the review queue",
    "IN_REVIEW": "being reviewed",
    "PENDING_DEVELOPER_RELEASE": "approved, waiting for you to release it",
    "PENDING_APPLE_RELEASE": "approved, Apple will release it",
    "PROCESSING_FOR_DISTRIBUTION": "being processed for the store",
    "PROCESSING_FOR_APP_STORE": "being processed for the store",
    "READY_FOR_DISTRIBUTION": "live",
    "READY_FOR_SALE": "live",
    "REJECTED": "rejected, needs attention",
    "METADATA_REJECTED": "metadata rejected, needs attention",
    "INVALID_BINARY": "invalid binary, needs attention",
    "DEVELOPER_REJECTED": "you withdrew it from review",
    "WAITING_FOR_EXPORT_COMPLIANCE": "waiting on export compliance",
    "REPLACED_WITH_NEW_VERSION": "replaced by a newer version",
    "DEVELOPER_REMOVED_FROM_SALE": "removed from sale by you",
}
UPLOAD_OK = "COMPLETE"


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def day(value: str | None) -> str:
    return (value or "")[:10] or "-"


# -- Status ---------------------------------------------------------------------


def version_state(attributes: dict) -> str | None:
    # appVersionState is the documented attribute; appStoreState is its older
    # sibling, which Apple still returns with different value names.
    return attributes.get("appVersionState") or attributes.get("appStoreState")


def unfinished_uploads(uploads: list[dict], since: datetime | None) -> list[dict]:
    """Uploads that did not complete and are newer than `since` (normally the
    latest build that did process). Older failures are history: an old failed
    upload says nothing about whether the current build is fine.
    """
    result = []
    for upload in uploads:
        attrs = upload["attributes"]
        state = (attrs.get("state") or {}).get("state")
        if state == UPLOAD_OK:
            continue
        when = parse_date(attrs.get("uploadedDate")) or parse_date(attrs.get("createdDate"))
        if since is not None and when is not None and when <= since:
            continue
        result.append(upload)
    return result


def describe_upload(upload: dict) -> list[str]:
    attrs = upload["attributes"]
    state = attrs.get("state") or {}
    when = day(attrs.get("uploadedDate")) if attrs.get("uploadedDate") else f"created {day(attrs.get('createdDate'))}, not uploaded"
    lines = [
        f"build {attrs.get('cfBundleVersion')}  v{attrs.get('cfBundleShortVersionString')}  "
        f"{state.get('state')}  {when}"
    ]
    for error in state.get("errors") or []:
        lines.append(f"  error {error.get('code')}: {(error.get('description') or '').strip()}")
    warnings = state.get("warnings") or []
    if warnings:
        lines.append(f"  {len(warnings)} warning(s), e.g. {warnings[0].get('code')}: "
                     f"{(warnings[0].get('description') or '').strip()}")
    return lines


def explain_missing_build(uploads: list[dict], ci_runs: list[dict] | None, build_number: str | None) -> list[str]:
    """Why a build that is not in the processed list is not there yet,
    from what Apple (and Xcode Cloud, when used) say about it.
    """
    lines = []
    wanted = [u for u in uploads if build_number is None or u["attributes"].get("cfBundleVersion") == build_number]
    if wanted:
        lines.append("Apple's record of the upload(s):")
        for upload in wanted[:5]:
            lines.extend(f"  {line}" for line in describe_upload(upload))
    elif build_number:
        lines.append(f"Apple has no upload for build {build_number}: nothing has reached App Store Connect yet.")
    if ci_runs:
        match = next((r for r in ci_runs if str(r["attributes"].get("number")) == str(build_number)), None)
        if match is not None:
            lines.append("Xcode Cloud: " + describe_ci_run(match))
        else:
            lines.append("Xcode Cloud's latest run: " + describe_ci_run(ci_runs[0]))
    return lines


def describe_ci_run(run: dict) -> str:
    a = run["attributes"]
    outcome = a.get("completionStatus") or a.get("executionProgress")
    return f"run #{a.get('number')}  {outcome}  {day(a.get('createdDate'))}  ({a.get('startReason')})"


def render_status(name: str, app_id: str, data: dict) -> str:
    out = [f"{name}  (id {app_id})"]

    versions = data.get("versions")
    if versions is not None:
        out.append("  Versions")
        seen_live = False
        for version in versions:
            attrs = version["attributes"]
            state = version_state(attrs)
            hint = STATE_HINTS.get(state, "")
            if hint == "live":
                # Apple keeps reporting older releases with the same state;
                # only the newest of them is what the store serves.
                hint, seen_live = ("earlier release" if seen_live else "live"), True
            out.append(f"    {attrs.get('versionString'):<10} {state}   {hint}".rstrip())

    build = data.get("build")
    if build is not None:
        a = build["attributes"]
        out.append(f"  Latest build     {a.get('version')}  ({a.get('processingState')}, uploaded {day(a.get('uploadedDate'))})")

    uploads = data.get("uploads")
    if uploads is not None:
        problems = data.get("problem_uploads", [])
        if not problems:
            out.append("  Build uploads    nothing stuck or failed since the latest processed build")
        else:
            out.append("  Build uploads    NEEDS ATTENTION")
            for upload in problems:
                out.extend(f"      {line}" for line in describe_upload(upload))

    if "ci_runs" in data:
        runs = data["ci_runs"]
        if runs is None:
            out.append("  Xcode Cloud      not set up for this app")
        elif not runs:
            out.append("  Xcode Cloud      no runs yet")
        else:
            out.append(f"  Xcode Cloud      {describe_ci_run(runs[0])}")

    reviews = data.get("reviews")
    if reviews is not None:
        out.append(f"  Customer reviews {reviews['unanswered']} written review(s) awaiting a reply")

    for note in data.get("notes", []):
        out.append(f"  (not available: {note})")
    return "\n".join(out)


# -- Localization coverage ------------------------------------------------------


def _language(locale: str) -> str:
    return locale.split("-")[0].lower()


def regional_stand_in(locale: str, present: set) -> str | None:
    """A present locale in the same language but another region (en-GB for a
    missing en-US). Chinese is excluded on purpose: Simplified and Traditional
    are different scripts, not regional variants of one another.
    """
    language = _language(locale)
    if language == "zh":
        return None
    return next((p for p in sorted(present) if p != locale and _language(p) == language), None)


@dataclass
class Coverage:
    kind: str
    name: str
    present: set
    expected: set

    @property
    def missing(self) -> list[str]:
        return sorted(self.expected - self.present)

    @property
    def region_only(self) -> dict:
        """Missing locales whose language is covered by another region."""
        found = {loc: regional_stand_in(loc, self.present) for loc in self.missing}
        return {loc: alt for loc, alt in found.items() if alt}

    @property
    def absent(self) -> list[str]:
        """Missing locales whose language is not covered at all: real gaps."""
        stand_ins = self.region_only
        return [loc for loc in self.missing if loc not in stand_ins]

    @property
    def ok(self) -> bool:
        return not self.missing


def listing_field_coverage(localizations: dict, expected: set) -> list[Coverage]:
    """One row per listing field: which of the expected locales have a
    non-empty value. `localizations` maps locale -> API resource.
    """
    rows = []
    for attribute, label in (("description", "description"), ("keywords", "keywords"), ("whatsNew", "What's New")):
        present = {
            locale for locale, res in localizations.items()
            if normalize(res["attributes"].get(attribute))
        }
        rows.append(Coverage("Listing", label, present, expected))
    return rows


def app_info_field_coverage(localizations: dict, expected: set) -> list[Coverage]:
    rows = []
    for attribute in ("name", "subtitle"):
        present = {
            locale for locale, res in localizations.items()
            if normalize(res["attributes"].get(attribute))
        }
        rows.append(Coverage("App info", attribute, present, expected))
    return rows


def render_coverage(name: str, source_note: str, expected: set, rows: list[Coverage]) -> str:
    out = [f"{name}   {len(expected)} expected locale(s) {source_note}"]
    out.append(f"  {', '.join(sorted(expected))}")
    gaps = notes = 0
    for row in rows:
        label = f"{row.kind}: {row.name}"
        have = len(row.expected & row.present)
        counts = f"{have}/{len(row.expected)}"
        if row.ok:
            out.append(f"  ok   {label:<48} {counts}")
            continue
        parts = []
        if row.absent:
            parts.append(f"missing: {', '.join(row.absent)}")
        if row.region_only:
            shown = ", ".join(f"{loc} (has {alt})" for loc, alt in row.region_only.items())
            parts.append(f"region only: {shown}")
        if row.absent:
            gaps += 1
            out.append(f"  GAP  {label:<48} {counts}   " + "; ".join(parts))
        else:
            notes += 1
            out.append(f"  ~    {label:<48} {counts}   " + "; ".join(parts))
    if rows:
        out.append(f"  {gaps} gap(s), {notes} region-only note(s), in {len(rows)} check(s)")
    else:
        out.append("  nothing to check")
    return "\n".join(out)


# -- Reviews ---------------------------------------------------------------------


def stars(rating: int | None) -> str:
    return f"{rating}/5" if rating else "?/5"


def render_review(review: dict, responses: dict, body_chars: int = 500) -> str:
    a = review["attributes"]
    response_id = (review.get("relationships", {}).get("response", {}).get("data") or {}).get("id")
    response = responses.get(response_id) if response_id else None
    body = (a.get("body") or "").strip()
    if len(body) > body_chars:
        body = body[:body_chars].rstrip() + " [...]"
    lines = [
        f"[{stars(a.get('rating'))}] {day(a.get('createdDate'))}  {a.get('territory')}  "
        f"\"{(a.get('title') or '').strip()}\" -- {a.get('reviewerNickname')}",
        *(f"    {line}" for line in body.splitlines() or [""]),
        f"    review id: {review['id']}",
    ]
    if response is None:
        lines.append("    reply: none")
    else:
        ra = response["attributes"]
        lines.append(f"    reply ({ra.get('state')}, response id {response['id']}):")
        lines.extend(f"      {line}" for line in (ra.get("responseBody") or "").strip().splitlines())
    return "\n".join(lines)
