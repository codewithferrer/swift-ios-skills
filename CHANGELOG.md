# Changelog

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
