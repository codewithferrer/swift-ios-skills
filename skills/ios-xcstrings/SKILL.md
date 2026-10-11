---
name: ios-xcstrings
description: Check, translate and update Xcode String Catalogs (.xcstrings) - per-language coverage, format-specifier mismatches that crash or garble text at runtime, plural forms a language needs, stale keys that are in fact still used, and a safe way to write translations that changes only the keys you translate and leaves Xcode's formatting untouched. Use whenever the user wants to translate or localize an iOS app, add a language, find untranslated or needs-review strings, validate a Localizable.xcstrings or InfoPlist.xcstrings, fix a catalog diff that touched thousands of lines, or asks how complete the localization is, even if they never say xcstrings.
compatibility: Python 3.9+. On a Mac with Xcode it also asks Xcode's own xcstringstool to read the result. No network and no credentials; it only reads and writes the catalog you point it at.
---

# iOS String Catalogs

Three commands over `Localizable.xcstrings` (and `InfoPlist.xcstrings`, widgets,
any other catalog). The translating itself is yours: the tool finds what needs
translating, hands over the context, checks what you wrote, and writes it in a
way that leaves the rest of the file byte for byte as it was.

| Command | What it does | Writes? |
|---------|--------------|---------|
| `catalog.py audit [CATALOG ...]` | Coverage per language and what is wrong | No |
| `catalog.py todo --catalog C --locale de` | The keys that need a translation, as JSON, with context | No |
| `catalog.py apply --catalog C --file F` | Put translations in; `--write` to save | Only with `--write` |

Run from the project folder. Without a path, `audit` finds every catalog under
it and skips build folders, `DerivedData` and `.claude`.

## Translating a language

1. **See where things stand.** `catalog.py audit --locales de`. Read the errors
   first, then the missing keys. A language that is not in the catalog yet is
   all missing, which is how a new language starts.
2. **Get the work list.**
   `catalog.py todo --catalog C --locale de --reference es --examples 15 --out de.todo.json`.
   Each item has the source text, the developer's `comment` (the context a
   translator needs), the placeholders to keep, and with `--reference` how the
   author already resolved it in another language. `--examples` adds translated
   pairs for tone and terminology.
3. **Translate.** Write `{"language": "de", "translations": {"key": "text"}}`.
   Read `references/translation-guide.md` for languages you have not done in
   this project; the points that bite are below.
4. **Preview.** `catalog.py apply --catalog C --file de.json`. Show the user
   the changes; nothing is written yet.
5. **Write.** Add `--write`. States are recorded as `needs_review` unless you
   pass `--state translated`; see the next section.
6. **Check again.** `catalog.py audit --locales de` must show 0 errors. Then a
   build in Xcode, which is the real test of the file.

Keys that use device variations or substitutions are listed under
`not_handled` and left for Xcode; everything else, including plurals, is
written here.

## States: why `needs_review` is the default

Xcode keeps a `needs_review` string flagged in its catalog editor until
someone marks it reviewed, which is exactly what a translation written without a
native speaker reading it needs. It still ships: the compiled catalog contains
the text whatever the state. So the honest default is `needs_review`, and the
user promotes strings to `translated` in Xcode after a spot check. Pass
`--state translated` only when the user says the text has been reviewed.

Existing `translated` values are never replaced unless `--overwrite` is given,
because that is a person's work.

## Rules that prevent the bugs this tool exists for

- **Keep every format specifier** (`%@`, `%lld`, `%1$@`, `%.1f`). A missing,
  extra or retyped one crashes or prints garbage. To reorder arguments in a
  language, use positional ones: `%2$@ ... %1$lld`. `apply` refuses the whole
  file if any translation breaks this, and tells you which key and why.
- **A literal percent sign is `%%`.** "50% off" written bare can be read as a
  placeholder (`% o`). The audit warns about it.
- **Keep line breaks and edge spaces** as in the source.
- **Plural keys need the language's own categories**: one and other for
  English or German, only other for Japanese or Chinese, one/few/many/other for
  Russian, six for Arabic. `todo` names them per item.
- **Never rewrite a catalog with a JSON library.** Xcode's writer differs from
  the default of every library, so a two-key change becomes a rewrite of the
  whole file. This tool re-emits the file in the format it already has and
  refuses to write if it cannot reproduce the original exactly.

## What the audit reports

| Code | Severity | Meaning |
|------|----------|---------|
| `placeholder_mismatch` | error | A translation's specifiers do not match the source |
| `duplicate_key` | error | The same key twice in the file; the later one silently wins |
| `xcode_rejects` | error | Xcode's compiler cannot read the file |
| `missing`, `new`, `needs_review` | warning | Not translated, stub from Xcode, or flagged for review |
| `plural_incomplete` | warning | A plural form the language needs is absent |
| `percent_space` | warning | A `% x` in text that a format string reads as a placeholder |
| `whitespace_differs`, `newline_differs` | warning | Edges or line breaks changed |
| `no_source_value` | warning | An identifier-like key with no source text; the UI shows the key |
| `stale_unreferenced` | warning | Stale and no literal match in the Swift sources |
| `stale_in_use` | info | Stale, but the text appears in the sources |
| `identical_to_source`, `much_longer` | info | Possibly untranslated; possible truncation |

Exit status is 1 when there is an error (`--strict`: any warning). `--json` for
scripts, `--all` to list every key instead of the first five.

## Gotchas

- **A language belongs to a catalog, not to the app.** The widget extension,
  `InfoPlist.xcstrings` (the app name and the permission prompts the system
  shows) and any other catalog each need it, and a catalog nobody added the
  language to simply shows English there. Run `audit` with no path so every
  catalog in the project is listed.
- **A language that is only in the catalog is built.** In a build of a real
  project the app bundle contained `de.lproj` with the German text although the
  project's `knownRegions` did not list German, so adding a language to the
  catalogs is enough for the app. The App Store listing languages are separate
  (App Store Connect; the `app-store-release` skill covers them).
- **Stale does not mean unused.** Xcode marks a key stale when it cannot see
  it in the code, which includes every key passed around in a variable. In one
  real catalog 322 of 326 stale keys were still used. Do not delete keys
  because of the mark; the audit cross-checks the Swift sources for this.
- **Xcode adds stubs on every build.** A key added to the code appears in the
  catalog as an English-only entry the next time the project builds. Translate
  into that entry (build first, then `apply`); a hand-added second copy of the
  key is silently overridden by Xcode's. `audit` finds duplicates, and `apply`
  refuses to write while one exists.
- **The source language is not edited here.** Its text lives in the code or in
  Xcode; a key whose source is the key itself (`Text("Hello")`) is translated
  with the key text as source.
- **Xcode's compiler is lenient.** `xcstringstool` accepts an unknown state and
  a placeholder that does not match; it only proves Xcode can read the file.
  The audit is what checks meaning.
- **A translation file with problems writes nothing.** Fix the listed keys and
  run it again; there is no partial apply to clean up.

## Reference files

| File | Read it when |
|------|--------------|
| `references/translation-guide.md` | Translating into a language for the first time here, or when unsure about tone, plurals, length or script |
| `references/catalog-format.md` | A catalog is refused as not reproducible, or you need to know how entries, states and variations are structured |
