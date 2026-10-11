# Changelog

## v0.11.0

### Skills

- Add `ios-analytics`: understand, audit and extend an iOS app's analytics.
  `analytics_scan.py` reads the Swift sources (no build needed) and recovers
  the whole chain from the SDK call through a private wrapper and a typed
  facade to the views that call it. `inventory` lists events, parameters,
  callers and the values callers pass; `coverage` finds screens, controls
  (following the functions they call, `onChange` and property observers),
  permission prompts, purchase flows, App Intents and caught errors that leave
  no record, ranked by what a blind spot costs; `check` flags invalid or
  reserved names, unbounded parameter values, personal data, double-counted
  screens, the custom dimension budget and missing privacy manifests against
  Google Analytics 4's documented limits; `plan` compares an event table in a
  product document with the code and ranks the places each missing event
  would go. Four references cover measurement planning, instrumentation
  points on iOS, Firebase/GA4 rules with sources, and privacy.
## v0.10.0

### Skills

- Add `ios-xcstrings`: check, translate and update Xcode String Catalogs.
  `catalog.py audit` reports coverage per language and what is wrong
  (placeholder mismatches, duplicate keys, plural forms a language needs,
  literal percent signs a format string would read as a placeholder, and stale
  keys cross-checked against the Swift sources, because Xcode marks keys stale
  that are in use). `todo` gives the work list for a language with the
  developer's comments, placeholders and the existing translations in a
  reference language. `apply` validates a translation file and writes only the
  keys in it, in the exact format the catalog already has, refusing to write
  when the file cannot be reproduced byte for byte, and checking the result
  with Xcode's `xcstringstool` where available. Translations are recorded as
  `needs_review` unless told otherwise. Needs no network and no credentials.
## v0.9.0

### Skills

- Add `ios-release-train`: the repository side of shipping an iOS version.
  `release_train.py` reads the iOS app target's version and build from the
  project file (tied to target and configuration, and confirmed against
  `xcodebuild -showBuildSettings`) instead of taking the first match, runs the
  guards a release tag needs (branch, clean tree, HEAD on origin, new tag,
  higher build), bumps the build only on the targets that carry it, closes the
  changelog, and creates and pushes just the release tag. Everything that
  writes or pushes is a preview until `--write` or `--push` is given. Needs no
  App Store Connect access.
## v0.8.0

### Skills

- `app-store-release`: `push_metadata.py` and `pull_metadata.py` now handle the
  version's copyright (`copyright.txt`) and its App Review information
  (`review_information/`: contact name, phone, email, demo account, notes), the
  two things that block a first submission and that the skill could not write.
  An existing App Review record is updated field by field, a missing one is
  created. `--fields copyright,review_information` sends just those.
- `app-store-release`: the demo account password is never printed and never
  pulled, and the push warns when its file sits in a git repository without
  being ignored. Bad input (a demo user without a password, a malformed email,
  notes over the limit) is refused before any request.
## v0.7.1

### Skills

- `ios-aso`: the storefront table grows from 15 to 30 rows, taken from Apple's
  App Store localizations page, and now covers Latin America, the German-speaking
  markets, the Gulf and Egypt, Ireland, Singapore, Belgium, Taiwan and Hong Kong.
  It records that es-MX is the default of most of Latin America and en-GB the
  default of six storefronts, which changes which localization is worth writing.
- `ios-aso`: `research.py markets` knows the language of the Latin American
  storefronts, so "local lang" is no longer `n/a` for them.
- `app-store-release`: `push_metadata.py` now handles adding a new locale. App
  Store Connect creates the app info localization by itself, copying the primary
  language's name and subtitle, as soon as the version localization exists; the
  push used to try to create it again and failed with a 409 after the version
  part had already been written. It now looks again, updates only what differs,
  and reports what already matched. Found by creating a locale on a real app.
## v0.7.0

### Skills

- `ios-aso`: `research.py markets` compares several storefronts side by side
  with the same signals (cohort size, median ratings and languages, how many
  serve the local language, name matches, small apps above their weight, stale
  apps, new entrants, and how local the field is), with terms written natively
  per market. The reference gains a section on reading it and on the costs
  of entering a market, including mainland China's ICP requirement.
- `ios-aso`: name matching now works for languages written without spaces
  (Japanese, Chinese), and "above their weight" is not reported when most of
  the cohort has no ratings, where it would only mean "unrated".
## v0.6.0

### Skills

- `ios-aso`: competitive research from Apple's public data. `research.py search`
  shows, per term, who ranks; the apps present across several terms; small apps
  ranking above their weight; stale apps that still rank; new entrants; cohort
  medians; and the vocabulary their names share that your listing lacks.
  `profile` compares specific apps with yours; `reviews` summarises what
  users praise and complain about. The client spaces its requests, has a hard
  budget, and stores nothing, in line with Apple's terms. New
  `references/competitor-research.md` covers the method and its pitfalls.
## v0.5.0

### Skills

- Add `ios-aso`: iOS-only App Store optimization. `aso_audit.py` checks a
  metadata folder for budget use, format problems, keywords already covered by
  the name or subtitle, and, per storefront, which localizations feed it and
  where the same word is spent twice. `keyword_pack.py` fills the
  100-character keyword field in priority order without repeats. References
  cover how search works (every claim labelled Apple-stated, observed or
  industry-reported), keyword strategy, conversion, and measurement.
## v0.4.0

### Skills

- `app-store-release`: `readiness.py` is a read-only pre-submission check. It
  reports what App Store Connect would still ask for (build and export
  compliance, listing text, screenshots, copyright, privacy policy URL,
  category, age rating, content rights, App Review contact, price schedule),
  keeps "could not be checked" apart from "passed", and lists what the API
  cannot see (App Privacy answers, agreements, tax and banking).
- `app-store-release`: `audit_localizations.py` no longer reports "What's New"
  as missing on an app's first version.
- `app-store-release`: network failures now surface as a clean error instead of
  a traceback. Reads are retried once; writes never are.
- `app-store-release`: `create_version.py` closes with the right next steps
  for a first version.

## v0.3.0

### Skills

- `app-store-release`: `status.py` reports where each app stands in one
  read-only view: newest versions and their state, latest build, uploads that
  failed or got stuck since then (with Apple's error codes), latest Xcode Cloud
  run, and reviews awaiting a reply.
- `app-store-release`: `audit_localizations.py` finds localization gaps across
  the listing, app name and subtitle, in-app purchases, and Game Center
  leaderboards and achievements, telling a missing language apart from one
  that is only present in another region.
- `app-store-release`: `reviews.py` lists written customer reviews and posts or
  deletes public replies, previewing by default. New `references/reviews.md`
  covers triage and how to write replies.
- `app-store-release`: `create_version.py` now explains why a build is missing,
  from Apple's upload record and the latest Xcode Cloud run, instead of only
  reporting it as not found.
- `app-store-release`: API errors carry their HTTP status, so callers can tell
  an expected 404 from a real failure.

## v0.2.0

### Skills

- `app-store-release`: draft release notes from the git history. New
  `collect_changes.py` lists the commits since the last tag, marks each by the
  kinds of files it touched, sets version-bump commits aside and shows the
  previous notes for tone. New `references/release-notes.md` covers what to
  keep, style, translating and file structure.
- `app-store-release`: `push_whats_new.py` now blocks text over 4000 characters
  or containing emoji before any API call, also in `--dry-run`, so a dry run
  doubles as the validator for freshly written notes. `--allow-emoji`
  overrides the emoji check.

## v0.1.0

### Skills

- Add `app-store-release`: App Store Connect release chores through Apple's
  API. Create a version and attach a build, push "What's New", pull and push
  store listing text with a field-by-field diff, upload screenshots, submit for
  review, and release after approval. Credentials and the app registry live
  outside the skill folder.

### Repository

- Claude Code plugin marketplace generated from `skills/`, with one plugin per
  skill and an `all-skills` bundle.
- `scripts/skills_repo.py` to validate skills, the marketplace and the README
  catalog, run in CI.
