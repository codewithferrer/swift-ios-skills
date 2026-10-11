# Usage

Exact commands for each script. Every script also has `--help`.

Every script takes `--app` (omit it when the apps file has one app) and
`--version` (the exact `versionString`, e.g. `1.9.2`).

## Before submitting - `readiness.py`

Read-only. Lists what App Store Connect would still ask for before the version
can be submitted, in three groups: *needs fixing*, *could not be checked* and
*passed*, plus a few advisory items.

```bash
python3 scripts/readiness.py --app my-app
python3 scripts/readiness.py --app my-app --version 1.0.0
python3 scripts/readiness.py --all --strict      # exit 1 when something must be fixed
```

It checks the attached build and its export compliance; description, keywords
and support URL per locale (and What's New after the first release);
screenshots in the primary locale (other locales fall back to it); copyright;
privacy policy URL; primary category; age rating; content rights declaration;
App Review contact details; and the price schedule. Advisory: subtitle,
accessibility declarations, and in-app purchase / subscription states.

A section the key cannot read is "could not be checked" and is never counted
as a pass. App Privacy answers and agreements, tax and banking status are not
exposed by the API, so the report lists them as things to check by hand.

## Checks - `status.py`, `audit_localizations.py`

Both are read-only.

```bash
python3 scripts/status.py --app my-app
python3 scripts/status.py --all
python3 scripts/status.py --app my-app --versions 5
```

`status.py` reports, per app: the newest versions with their state and what it
means, the latest build, any upload newer than the latest processed build that
failed or is stuck (with Apple's error codes), the latest Xcode Cloud run, and
how many written reviews still have no reply. A section the API key cannot read
is listed as "not available" instead of aborting the report.

```bash
python3 scripts/audit_localizations.py --app my-app
python3 scripts/audit_localizations.py --all --strict
python3 scripts/audit_localizations.py --app my-app --version 1.9.2 --locales en-US,es-ES,ja
```

`audit_localizations.py` compares, against the locales on the version's
listing (or `--locales`): the listing's description, keywords and What's New,
the app name and subtitle, every in-app purchase, and every Game Center
leaderboard and achievement. `GAP` is a language that is missing outright; `~`
is a language present only in another region (for example `en-GB` where
`en-US` is expected). `--strict` exits 1 when any `GAP` exists.

## Customer reviews - `reviews.py`

```bash
python3 scripts/reviews.py list --app my-app
python3 scripts/reviews.py list --app my-app --unanswered --rating 1 --territory USA
python3 scripts/reviews.py list --app my-app --summary
python3 scripts/reviews.py reply --review <id> --body-file reply.txt          # preview
python3 scripts/reviews.py reply --review <id> --body-file reply.txt --send   # posts
python3 scripts/reviews.py delete-reply --response <id> --delete
```

`reply` and `delete-reply` change public text, so they preview by default and
need `--send` / `--delete`. Drafting guidance is in `references/reviews.md`.

## 0. Gather what changed - `collect_changes.py`

Read-only: runs `git log`, never contacts App Store Connect, needs no
credentials. Lists the commits since the latest tag, each marked by the kinds of
files it touched, plus the project's version and build numbers and the previous
notes for tone. Drafting from it is covered in `references/release-notes.md`.

```bash
python3 scripts/collect_changes.py --app my-app
python3 scripts/collect_changes.py --app my-app --since release.1.9.0.20 --bodies
python3 scripts/collect_changes.py --repo ~/code/my-app --tag-pattern 'release.*' --path Sources/
```

With `--app` it works in the git repository that holds the app's release notes
file; with `--repo` it needs no apps file at all. `--keep-noise` stops it setting
"Upgrade version"-style commits aside.

## 1. Create the version and attach a build - `create_version.py`

```bash
# No --build: lists processed builds so you can choose one
python3 scripts/create_version.py --app my-app --version 1.9.3

python3 scripts/create_version.py --app my-app --version 1.9.3 --build 26 --dry-run
python3 scripts/create_version.py --app my-app --version 1.9.3 --build 26        # asks first
python3 scripts/create_version.py --app my-app --version 1.9.3 --build 26 --yes  # no prompt
```

If the version already exists it is not duplicated; the build is just attached.
`--release-type` is `manual` (default, needs step 6), `after_approval`, or
`scheduled`.

## 2. Push "What's New" - `push_whats_new.py`

```bash
python3 scripts/push_whats_new.py --app my-app --version 1.9.3 --dry-run   # no API key needed
python3 scripts/push_whats_new.py --app my-app --version 1.9.3             # asks first
```

The version must already exist and still be editable.

## 3. Store listing text - `pull_metadata.py` / `push_metadata.py`

Name, subtitle, privacy policy URL, description, keywords, promotional text,
support URL and marketing URL, kept as plain text files (layout below).

Start by pulling what is live. This bootstraps the folder and doubles as a
backup:

```bash
python3 scripts/pull_metadata.py --app my-app --version 1.9.2 --dry-run
python3 scripts/pull_metadata.py --app my-app --version 1.9.2
```

Edit the files, then see exactly what would change and push it:

```bash
python3 scripts/push_metadata.py --app my-app --version 1.9.3 --dry-run
python3 scripts/push_metadata.py --app my-app --version 1.9.3
python3 scripts/push_metadata.py --app my-app --version 1.9.3 --fields promotional_text --locales en-US
```

- Only fields that differ from App Store Connect are written; the rest are
  reported as unchanged.
- Text over Apple's character limits is rejected before any API call.
- `pull_metadata.py` never overwrites a local file whose content differs unless
  you pass `--force`.
- Per-version fields (description, keywords, promotional text, URLs) need the
  version to exist and be editable. App-level fields (name, subtitle, privacy
  URL) need an app info in an editable state, which Apple exposes while a
  version is being prepared; when there is none they are reported as skipped and
  the per-version fields are still pushed.
- `--locales` and `--fields` narrow a run. Apps-file `skip_locales` apply too.
- `copyright.txt` and `review_information/` (App Review contact, demo account,
  notes) are pushed and pulled by the same scripts; see `metadata-files.md`.
  `--fields copyright,review_information` sends just those.

## 4. Upload screenshots - `upload_screenshots.py`

```bash
python3 scripts/upload_screenshots.py --app my-app --version 1.9.3 \
    --locale en-US --dir ./screenshots --dry-run
python3 scripts/upload_screenshots.py --app my-app --version 1.9.3 \
    --locale en-US --dir ./screenshots --replace
```

Files upload in sorted filename order, which becomes the display order (name
them `01_...png`, `02_...png`). A new version inherits the previous version's
screenshots, so the set is rarely empty; without `--replace` the script stops
rather than mixing old and new.

## 5. Submit for review - `submit_for_review.py`

Apple's API splits submission into three calls and the last one is
irreversible, so this script does nothing by default:

```bash
python3 scripts/submit_for_review.py --app my-app --version 1.9.3             # preview only
python3 scripts/submit_for_review.py --app my-app --version 1.9.3 --prepare   # draft, reversible
python3 scripts/submit_for_review.py --app my-app --version 1.9.3 --submit    # sends to Apple
```

`--submit` also makes you type the version string to confirm.

## 6. Release after approval - `release_version.py`

For versions created with `--release-type manual`. Acts only when the version
is `PENDING_DEVELOPER_RELEASE`; there is no API undo.

```bash
python3 scripts/release_version.py --app my-app --version 1.9.3            # preview only
python3 scripts/release_version.py --app my-app --version 1.9.3 --release  # publishes
```

The `RELEASE_NOTES.md` format used by `push_whats_new.py` is described in `SKILL.md`.
