---
name: app-store-release
description: Automate App Store Connect release chores through Apple's official API instead of the website: check where a version, build upload or review stands, whether a version is ready to submit, and why a build failed, draft release notes from git history and push them as localized "What's New", pull and push store listing text with the copyright and the App Review contact, find missing localizations (listing, in-app purchases, Game Center), create versions and attach builds, upload screenshots, submit for App Review, release approved versions, and read or reply to customer reviews. Use whenever the user wants to ship, release or submit an iOS app version, asks where a version or build stands or why a build is missing, or wants to know what is missing before submitting, or wants release notes, listing text, localization gaps, screenshots or review replies handled, even if they never mention the API or this skill by name.
compatibility: Python 3.9+ with requests and PyJWT[crypto]; needs network access to api.appstoreconnect.apple.com and an App Store Connect API key (App Manager role).
---

# App Store release

Wraps a handful of release-day chores in small Python scripts that talk to the
App Store Connect API (JWT auth with a team API key). It does not upload
binaries - builds still arrive through Xcode, Xcode Cloud or Transporter - it
automates what happens in App Store Connect around them.

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
| 0 | `collect_changes.py --app A` then write `RELEASE_NOTES.md` | Read-only. Lists commits since the last tag, sorted by what they touch. You draft the notes from it, following `references/release-notes.md`. |
| 1 | `create_version.py --app A --version V [--build N]` | Creates the version record (if missing) and attaches the processed build. Without `--build` it lists candidate builds. |
| 2 | `push_whats_new.py --app A --version V` | Writes "What's New" for every locale in the app's release notes file. |
| 3 | `push_metadata.py --app A --version V [--fields F] [--locales L]` | Writes the listing text files (description, keywords, name, subtitle, ...) that differ from App Store Connect, plus `copyright.txt` and the `review_information/` contact files. |
| 4 | `upload_screenshots.py --app A --version V --locale L --dir D [--replace]` | Uploads a folder of PNGs to a screenshot set, in filename order. |
| 5 | `submit_for_review.py --app A --version V --prepare` / `--submit` | `--prepare` builds a draft review submission; `--submit` sends it to Apple. |
| 6 | `release_version.py --app A --version V --release` | Publishes an approved version that was created with manual release. |

`--app` can be omitted when the apps file lists a single app. Every script has
`--help`.

Typical order is 0 (draft the notes) -> 1 -> 2 -> 3 -> 4 -> 5, then 6 only after Apple
approves. Step 0 can happen before or after step 1.
Steps 2-4 need the version to still be editable (not yet approved/released).

## Checking where things stand

Read-only, safe to run at any time and worth running before acting:

| Command | Answers |
|---------|---------|
| `status.py --app A` (or `--all`) | Newest versions and their review/release state, latest processed build, any upload since then that failed or got stuck (with Apple's error codes), latest Xcode Cloud run, reviews awaiting a reply. |
| `readiness.py --app A [--version V]` | What App Store Connect would still ask for before this version can be submitted: build and export compliance, listing text, screenshots, copyright, privacy URL, category, age rating, content rights, App Review contact, price. Run it before step 5. |
| `audit_localizations.py --app A` | Which listing fields, in-app purchases, Game Center leaderboards and achievements are missing a language the listing supports. |
| `reviews.py list --app A [--unanswered] [--summary]` | Written customer reviews, newest first, with any reply under each. |
| `pull_metadata.py --app A --version V` | Downloads the live listing text into the metadata folder. Bootstraps it and doubles as a backup. |

When `create_version.py` cannot find a build it now says why, from Apple's own
upload record and Xcode Cloud, so "where is my build" rarely needs the website.

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
- **Drafted notes are not approved notes.** Show the user the text you wrote
  and wait for their yes before `push_whats_new.py` sends it, even when they
  asked you to "write and push" them: translations in languages they cannot
  read are the part most worth a pause.
- **Writes need a clear yes from the user in this conversation**, naming the
  app and version. A request like "get 1.9.3 ready" authorizes steps 1-4 and
  `--prepare`, not `--submit`.
- **`--submit` and `--release` are irreversible.** Only run them when the user
  explicitly asked for that exact action on that exact app and version. Stop at
  `--prepare` otherwise and point them at the draft in App Store Connect.
- **`--yes` is for after consent, not instead of it.** Claude has no terminal to
  type into, so a confirmed write is run with `--yes`. Adding it before the
  user has seen the preview defeats the confirmation prompts.
- **A review reply is public text under the user's name.** `reviews.py reply`
  previews unless `--send` is given. Draft it, show it next to the review it
  answers, and post only after the user approves that specific reply. Tone
  and limits: `references/reviews.md`.
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
- **"What's New" is checked before sending.** Over 4000 characters, or any
  emoji, blocks `push_whats_new.py` (also in `--dry-run`, which makes it the
  validator for freshly drafted notes). `--allow-emoji` overrides the emoji
  check only.
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
- **Copyright and the App Review contact are per version, not per locale**, and
  are required to submit. They live in `copyright.txt` and
  `review_information/` (`references/metadata-files.md`). The demo password is
  never printed or pulled; remind the user to keep that file out of git.
- **Text over Apple's character limits** (name/subtitle 30, keywords 100,
  promotional text 170, description 4000) is rejected locally before any call.
- **`readiness.py` never turns "could not read" into a pass.** A section it cannot
  load shows as "could not be checked" and the verdict becomes UNCLEAR, not READY.
  It also cannot see App Privacy answers or agreements/tax/banking: say so when
  reporting, so "READY" is not read as "will be approved".
- **Screenshots: only the primary locale needs its own.** Other locales show the
  primary one's when they have none, so `readiness.py` does not count them as
  missing.
- **"Stuck or failed" means newer than the latest processed build.** `status.py`
  ignores older failed uploads, which are history and say nothing about the
  current build.
- **Audit `GAP` versus `~`.** `GAP` means the language is missing outright. `~`
  means only another region of that language exists (Game Center often has
  `en-GB` and no `en-US`, or `es-ES` and no `es-MX`). Chinese Simplified and
  Traditional are never treated as stand-ins for each other.
- **Only written reviews exist for the API.** Star-only ratings are not
  exposed, so zero reviews here does not mean zero ratings. A reply cannot be
  edited: delete it and post a new one.
- **A `403` on some endpoints is the key's role, not a bug.** Analytics reports
  and TestFlight tester lists refused an App Manager key during development.
  `status.py` shows such sections as "not available" instead of failing.
- **API errors are printed verbatim** with the HTTP status. A `409` usually
  means the object is in a state that does not allow the change (already in
  review, already released).

## Reference files

Read these only when the task needs them; everything needed for a normal
release is above.

| File | Read it when |
|------|--------------|
| `references/setup.md` | The user has no API key, credentials folder, apps file or virtualenv yet. |
| `references/release-notes.md` | Writing or rewriting release notes: what to keep, style, translating, file structure. |
| `references/reviews.md` | Answering customer reviews: how to triage, draft and post replies, and what never to say. |
| `references/usage.md` | You need the exact flags and examples for a script. |
| `references/metadata-files.md` | Creating or editing the listing text folder: file names, limits, what each file maps to. |

## Not covered

- **Other platforms.** Versions are created with platform `IOS`. macOS, tvOS or
  visionOS is not a single flag: version and build lookups also have to filter
  by platform, and there was no non-iOS app to verify against.
- **Everything else in App Store Connect**: categories, age rating, pricing,
  App Review information, in-app purchases and app previews.

Offline unit tests for the metadata logic: `python3 -m unittest discover -s tests`.
