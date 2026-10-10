# Usage

Exact commands for each script. Every script also has `--help`.

Every script takes `--app` (omit it when the apps file has one app) and
`--version` (the exact `versionString`, e.g. `1.9.2`).

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
