# Changelog

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
