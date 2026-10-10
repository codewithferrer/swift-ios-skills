# Changelog

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
