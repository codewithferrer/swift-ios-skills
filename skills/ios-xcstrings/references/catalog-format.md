# The String Catalog format

What a `.xcstrings` file contains and which parts a tool has to be careful
with. Everything here was checked against real catalogs written by several
Xcode versions; where the format is a convention of Xcode's writer rather than
JSON itself, it says so.

## Layout

```json
{
  "sourceLanguage" : "en",
  "strings" : {
    "home_title_key" : {
      "comment" : "Title of the home screen.",
      "extractionState" : "manual",
      "localizations" : {
        "de" : { "stringUnit" : { "state" : "translated", "value" : "Start" } },
        "en" : { "stringUnit" : { "state" : "translated", "value" : "Home" } }
      }
    }
  },
  "version" : "1.0"
}
```

(shown compact; Xcode writes every object over several lines)

- A space on **both** sides of the colon, two-space indentation, one key per
  line, sorted keys inside an entry (`comment`, `extractionState`,
  `localizations`, `shouldTranslate`; `state` before `value`).
- Non-ASCII text is written as is (UTF-8), not as `\uXXXX`. Quotes, backslash
  and control characters are escaped.
- **Not everything is settled by Xcode's version**: an empty object is written
  as `{`, a blank line, `}` by most files and as `{}` by some; the file may or
  may not end with a newline; the order of locales inside an entry is sorted in
  most files and not in others. A tool has to follow the file, not assume.
- `version` is `1.0`, `1.1` or `1.2` in files seen; it is Xcode's, not the
  app's.

## Entries

| Field | Meaning |
|-------|---------|
| key | The identifier, or the source text itself (`Text("Hello")` extracts the key `Hello`) |
| `comment` | The developer's note for translators; the best source of context there is |
| `extractionState` | `manual` (added by hand), `extracted_with_value` (Xcode found it with a default), `migrated`, `stale` (Xcode could not find it in the code at the last build) |
| `shouldTranslate` | `false` is "Don't Translate" |
| `localizations` | Language code to text |

An entry may be empty (`{}`): Xcode extracted a key that is its own source.
Empty entries for symbols and numbers (`"⌚"`, `"%lld"`) are common and need no
translation.

### The source text

If the source language has a value in `localizations`, that is the source
text. If not, the **key** is the source text. A catalog with identifier-like
keys (`home_title_key`) and no source value shows the key itself unless the
code passes `defaultValue:`.

## States of a translation

| `state` | Meaning |
|---------|---------|
| `new` | A stub; the language has no real translation yet |
| `translated` | Done |
| `needs_review` | Xcode (source changed) or a person flagged it; the text is used as is |
| `stale` is **not** a state | It is an `extractionState`, above |

## Variations

A localization is either a `stringUnit`, or `variations`, or has
`substitutions`:

```json
"localizations" : { "en" : { "variations" : { "plural" : {
    "one"   : { "stringUnit" : { "state" : "translated", "value" : "%lld item" } },
    "other" : { "stringUnit" : { "state" : "translated", "value" : "%lld items" } } } } } }
```

- `variations.plural` has one entry per CLDR category (`zero`, `one`, `two`,
  `few`, `many`, `other`). Each language needs its own set.
- `variations.device` has `iphone`, `ipad`, `mac`, `applewatch`, `appletv`,
  `applevision`, `other`.
- `substitutions` define named arguments (`%#@count@`) that carry their own
  plural variations.

The tool reads all of these for the audit and writes plain strings and
top-level plurals; device variations and substitutions are left for Xcode.

## Why a catalog is "not reproducible"

Before writing, the tool emits the parsed data and compares it with the file.
If they differ by one byte it will not write, because then a change of three
keys would also reformat everything else. Reasons seen or expected:

- A different indentation or separators, from a file that went through a
  generic JSON writer (`indent=4`, `": "`).
- Windows line endings.
- A key repeated: the parsed data keeps one copy, so the file cannot be
  reproduced. Remove the extra copy in Xcode.

The note in the audit shows the first line that differs.

## Xcode's compiler

`xcrun xcstringstool compile --output-directory DIR File.xcstrings` is what the
build runs. It fails on a file it cannot parse and on structure errors; it
**accepts** an unknown `state` and a translation whose placeholders do not
match the source. Use it as a floor, not as the check.
