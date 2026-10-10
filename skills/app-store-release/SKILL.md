---
name: app-store-release
description: Automate the repetitive App Store Connect chores of an iOS app release through Apple's official API instead of the website - create a version and attach an uploaded build, push localized "What's New" text from a RELEASE_NOTES.md, pull and push the store listing text (name, subtitle, description, keywords, promotional text, support and marketing URLs), upload App Store screenshots, submit for App Review, and release an approved version. Use whenever the user wants to ship, release, or submit an iOS app version, update release notes, "What's New", description, keywords or subtitle on App Store Connect, back up or version-control the store listing, upload App Store screenshots, create a new App Store version, or publish an approved build, even if they never mention the API or this skill by name.
compatibility: Python 3.9+ with requests and PyJWT[crypto]; needs network access to api.appstoreconnect.apple.com and an App Store Connect API key (App Manager role).
---

# App Store release

Wraps a handful of release-day chores in small Python scripts that talk to the
App Store Connect API (JWT auth with a team API key). It does not upload
binaries - the build still goes up through Xcode/Transporter - it automates
what happens in App Store Connect afterwards.

All commands below are run from this skill's directory, e.g.
`python3 scripts/create_version.py ...`. Use the skill's base directory path
when calling them from elsewhere.

## Before the first run

Nothing user-specific lives in the skill folder. Check these three things, and
if one is missing, walk the user through it with `references/setup.md`, which has
the full steps (API key, config folder, apps file, virtualenv):

1. **Credentials**: `ASC_KEY_ID` and `ASC_ISSUER_ID` in the environment or in
   `~/.config/app-store-release/.env`, plus the `AuthKey_<KEY_ID>.p8` file in
   `~/.config/app-store-release/keys/`. `ASC_CONFIG_DIR` overrides that
   directory. The key needs the App Manager role. Apple only lets you download
   the `.p8` once, so never suggest deleting or regenerating it casually.
2. **App registry**: an `asc-apps.json` in the project (or any parent
   directory) mapping an app key to its numeric Apple ID, its release notes
   file (`release_notes_path`) and its listing text folder (`metadata_path`).
   Format: `examples/asc-apps.example.json`.
3. **Dependencies**: `pip install -r requirements.txt` (`requests`,
   `PyJWT[crypto]`), ideally in a venv. The scripts print this command
   themselves when the import fails. `--help` and `push_whats_new.py --dry-run`
   work without them.

Never read, print, copy, or commit the contents of `.env` or the `.p8` file.

## The release flow

| Step | Command | What it changes |
|------|---------|-----------------|
| 1 | `create_version.py --app A --version V [--build N]` | Creates the version record (if missing) and attaches the processed build. Without `--build` it lists candidate builds. |
| 2 | `push_whats_new.py --app A --version V` | Writes "What's New" for every locale in the app's release notes file. |
| 3 | `push_metadata.py --app A --version V [--fields F] [--locales L]` | Writes the listing text files (description, keywords, name, subtitle, ...) that differ from App Store Connect. |
| 4 | `upload_screenshots.py --app A --version V --locale L --dir D [--replace]` | Uploads a folder of PNGs to a screenshot set, in filename order. |
| 5 | `submit_for_review.py --app A --version V --prepare` / `--submit` | `--prepare` builds a draft review submission; `--submit` sends it to Apple. |
| 6 | `release_version.py --app A --version V --release` | Publishes an approved version that was created with manual release. |
| - | `pull_metadata.py --app A --version V` | Read-only against Apple: downloads the live listing text into the metadata folder. Bootstraps it and doubles as a backup. |

`--app` can be omitted when the apps file lists a single app. Every script has
`--help`.

Typical order is 1 -> 2 -> 3 -> 4 -> 5, then 6 only after Apple approves.
Steps 2-4 need the version to still be editable (not yet approved/released).
`pull_metadata.py` can run at any time.

## Safety rules

These scripts change a live App Store listing, and two of them cannot be
undone. The rules exist so that a misread request never ends up as a submission
to Apple.

- **Preview first.** Run `--dry-run` (or, for `submit_for_review.py` and
  `release_version.py`, no action flag - dry-run is their default) and show the
  user what would change before any write. For `push_metadata.py` the preview
  is a field-by-field diff against what is live, which is the thing to review.
- **Back up before overwriting listing text.** Before the first
  `push_metadata.py` on an app, run `pull_metadata.py` so the previous text is
  on disk. Pushing replaces the text in App Store Connect and there is no undo.
- **Writes need a clear yes from the user in this conversation**, naming the
  app and version. A request like "get 1.9.3 ready" authorizes steps 1-4 and
  `--prepare`, not `--submit`.
- **`--submit` and `--release` are irreversible.** Only run them when the user
  explicitly asked for that exact action on that exact app and version. Stop at
  `--prepare` otherwise and point them at the draft in App Store Connect.
- **`--yes` is for after consent, not instead of it.** Claude has no terminal to
  type into, so a confirmed write is run with `--yes`. Adding it before the
  user has seen the preview defeats the confirmation prompts.
- **`--replace` deletes screenshots.** Say so before using it. New versions
  inherit the previous version's screenshots, so a non-empty set is normal and
  the script refuses to mix old and new without `--replace`.

## Release notes format

`push_whats_new.py` reads the file named by the app's `release_notes_path`.
One heading per locale, with the text in a fenced block directly below:

````markdown
## en-US (English)

```
What's new text, one or more lines.
```

## es-ES / es-MX (Spanish)

```
One block can serve several locales.
```
````

- The locale code(s) go before the parenthesis, in App Store Connect format
  (`en-US`, `es-ES`, `pt-BR`, `ja`). The parenthesised name is required but
  ignored.
- A heading with no fenced block under it is ignored, so a trailing
  "what's covered" section is safe.
- Locales that are not valid App Store Connect metadata locales go in the app's
  `skip_locales` list, or the API call for them fails partway through a run.

## Gotchas

- **Version must exist first.** `push_whats_new.py`, `push_metadata.py`,
  `pull_metadata.py`, `upload_screenshots.py` and `submit_for_review.py` fail
  with "No appStoreVersions found" until step 1 (or the web UI) created the
  version.
- **Build not found** means Apple has not finished processing it yet. Only
  builds in the `VALID` state are listed.
- **Review submission is three API calls** (container, item, `submitted=true`).
  `--prepare` stops before the last one on purpose.
- **`release_version.py` only acts in `PENDING_DEVELOPER_RELEASE`.** Versions
  created with `--release-type after_approval` or `scheduled` release
  themselves, so there is nothing for it to do.
- **Screenshot display type** defaults to `APP_IPHONE_65`. Apple's required
  sizes change over time, so pass `--display-type` (or set
  `screenshot_display_type` per app) using the exact `ScreenshotDisplayType`
  enum value, and check Apple's current screenshot specifications.
- **Listing text is files, "What's New" is not.** `push_metadata.py` ignores
  `release_notes.txt`; What's New always comes from `RELEASE_NOTES.md` through
  `push_whats_new.py`. Layout and limits: `references/metadata-files.md`.
- **Name, subtitle and privacy URL need an editable app info.** Apple exposes
  one only while a version is being prepared. When the app has none,
  `push_metadata.py` reports those fields as skipped and still pushes the
  per-version ones, so a "skipped: name, subtitle" line is not a failure.
- **A live version is mostly locked.** `push_metadata.py` prints a note when
  the version is already `READY_FOR_SALE`; expect `409` for most fields.
- **Text over Apple's character limits** (name/subtitle 30, keywords 100,
  promotional text 170, description 4000) is rejected locally before any call.
- **API errors are printed verbatim** with the HTTP status. A `409` usually
  means the object is in a state that does not allow the change (already in
  review, already released).

## Reference files

Read these only when the task needs them; everything needed for a normal
release is above.

| File | Read it when |
|------|--------------|
| `references/setup.md` | The user has no API key, credentials folder, apps file or virtualenv yet. |
| `references/usage.md` | You need the exact flags and examples for a script. |
| `references/metadata-files.md` | Creating or editing the listing text folder: file names, limits, what each file maps to. |

## Not covered

- **Other platforms.** Versions are created with platform `IOS`. macOS, tvOS or
  visionOS is not a single flag: version and build lookups also have to filter
  by platform, and there was no non-iOS app to verify against.
- **Everything else in App Store Connect**: categories, age rating, pricing,
  App Review information, in-app purchases and app previews.

Offline unit tests for the metadata logic: `python3 -m unittest discover -s tests`.
