# Writing release notes

How to turn the git history since the last release into the "What's New" copy
that `push_whats_new.py` sends. The writing is yours; `collect_changes.py`
only gathers and pre-sorts the evidence.

## Procedure

1. **Pin down the target.** The app key (`--app`) and the version the notes are
   for. `collect_changes.py` prints the project's `MARKETING_VERSION` and
   build number; if the version equals the latest tag's, nothing new has been
   versioned yet, so ask which version the notes are for.
2. **Collect.**
   ```bash
   python3 scripts/collect_changes.py --app my-app
   ```
   Add `--bodies` when commit subjects are cryptic, `--since <tag-or-sha>` to
   widen or narrow the range, `--tag-pattern 'release.*'` when other tags
   exist, and `--path <dir>` when one repository holds several apps.
3. **Decide what a user would notice** (below).
4. **Verify the doubtful ones.** `git show <sha>` or the diff settles whether a
   commit changes behaviour. Do not infer a feature from a subject alone, and
   do not invent anything the history does not show. If a change matters but
   you cannot tell what it does for the user, ask.
5. **Draft the source-language copy first**, matching the previous notes' tone
   and length (the collector prints the previous text).
6. **Translate** into every locale (below).
7. **Write the file**, keeping the structure it already has (below).
8. **Validate** with `python3 scripts/push_whats_new.py --app my-app --version V --dry-run`.
   It parses the file exactly as the real push will and blocks on the length
   limit and on emoji.
9. **Show the user** the source-language text, the list of locales, and the
   commits you deliberately left out. Wait for their approval before pushing.
   Drafted text is not approved text.

## What belongs in the notes

Keep what a person using the app can see or feel:

- new features and visible behaviour changes
- fixes for problems users could have hit
- changes users can feel: faster, steadier, smaller

Leave out what only the team can see:

- refactors, renames, dependency or build changes, test changes
- analytics, logging and tracking changes
- documentation, roadmap and changelog edits
- version bumps (the collector already sets the obvious ones aside)

A commit tagged `[docs]`, `[project]` or `[tests]` alone almost never belongs.
`[code]`, `[l10n]` and `[assets]` usually do, but a code commit can still be
internal, so check it.

## Style

- **Lead with the strongest change.** The product page shows only the first few
  lines before "more".
- **Short and concrete.** Three to six lines is typical; the limit is 4000
  characters and nobody reads that far. Say what the user gets, in plain words.
- **Match the established line style.** If the previous file is one sentence
  per line with no bullets, do the same.
- **No internals.** No class names, flags, ticket numbers or commit hashes, and
  no "refactored" or "improved architecture".
- **A closing "Bug fixes and polish" line is fine** when the release also has
  minor fixes. It should not be the only content when features shipped.
- **No emoji.** The check blocks them, and App Store Connect has been seen
  rejecting them in this field.
- **No other platforms or competitors**, no promises about unreleased features,
  and no prices or time-limited offers unless the user asked for them.

## Translating

- Produce a block for **every locale already in the notes file**, including any
  listed in the app's `skip_locales`; those are kept for other channels and
  are simply not pushed. If there is no file yet, ask which locales to cover,
  using App Store Connect locale codes (`en-US`, `es-ES`, `de-DE`, `ja`,
  `zh-Hans`, `ar-SA`...).
- Translate the meaning, not the words: natural phrasing for each language,
  native script and orthography, same number of lines, product and feature names
  as the app itself spells them in that language.
- Right-to-left languages are plain text; add no direction markers.
- The translations are model drafts. Say so, and name the languages the user is
  unlikely to be able to check so they can decide whether to have a native
  speaker look. Short, simple sentences keep that risk down.
- Language names in headings are ignored by the parser; follow the convention
  the file already uses.

## File structure

The format the parser needs is in `SKILL.md`. Beyond that, keep what the
existing file does:

- A heading with the version and build, for example `# Release Notes - v1.9.3 (26)`.
  Take the build number from `CURRENT_PROJECT_VERSION` or the tag.
- One version per file: replace the previous release's content rather than
  stacking versions, unless the file clearly keeps history.
- Any preamble notes (locale caveats, emoji warning) stay as they are.
- A trailing section for the team (for example "What's covered") is plain prose
  with no fenced block, so the parser ignores it. Refresh it with the technical
  summary and the tag the range started from.

When there is no file yet, start small: the heading, then one block per locale.
Create it at the app's `release_notes_path`, and tell the user where it is.
