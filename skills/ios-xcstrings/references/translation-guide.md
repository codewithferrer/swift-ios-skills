# Translating a String Catalog

Practical rules for the languages an iOS app usually ships. They are the
distilled version of what goes wrong; the tool catches the mechanical ones, so
this file is mostly about judgement.

## Before translating

1. Read the `comment` of every key. It says where the text appears, which is
   what decides between "Close" the verb and "Close" the adjective.
2. Use `--reference` to see how the author already translated each key into a
   language they own. It settles meaning and register, not wording.
3. Use `--examples` to see terminology already chosen in the target language,
   and stay with it. A product that says "Wecker" in one screen and "Alarm" in
   the next reads as two products.
4. Decide the register once (informal or formal address) and keep it. Match
   the existing languages: if the Spanish uses *tú*, use *du*, *tu*, *tu*.

## What must not change

- Format specifiers, exactly. Reorder only with positional ones (`%2$@`).
- Line breaks (`\n`) and leading or trailing spaces.
- Product, brand and feature names, unless the existing translations already
  localize them.
- Any `%%`: it is a literal percent sign.

## Terminology: use Apple's

For UI words that exist in iOS, use the word iOS uses in that language, because
people recognise it: *Settings*, *Cancel*, *Save*, *Delete*, *Focus*,
*Notifications*, *Camera*. The German Clock app calls alarms *Wecker* and the
label field *Bezeichnung*; the French one *Réveil*; the Japanese one
*アラーム*. Open the system app in that language when in doubt.

## Length

Translations are rarely the length of the English. Plan buttons and tabs for it:

| Language | Typically |
|----------|-----------|
| German, Finnish, Dutch | 30% to 40% longer; long compounds do not wrap well |
| French, Spanish, Italian, Portuguese | 15% to 30% longer |
| Russian, Greek | 20% to 30% longer |
| Japanese, Chinese | Much shorter in characters, but taller glyphs |
| Korean | Slightly shorter |
| Arabic | Similar, right to left |

The audit lists translations that grew by more than 80% as `much_longer`. For
those, prefer a shorter word to a layout change.

## Plurals

Give every category the language uses; the work list names them.

| Language | Categories |
|----------|------------|
| English, German, Spanish, French, Italian, Portuguese, Dutch, Catalan, Galician | one, other |
| Japanese, Korean, Chinese, Vietnamese, Thai, Indonesian | other |
| Russian, Ukrainian, Polish | one, few, many, other |
| Czech, Slovak | one, few, other |
| Arabic | zero, one, two, few, many, other |

`one` does not mean "exactly 1" in every language: in French it covers 0 and 1,
and in Russian it covers 21, 31, 101. Do not hard-code the number in that form
unless the language allows it; the `one` form may leave the number out ("Ein
Element") but `other` should keep it.

## Script and direction

- **Chinese**: write Simplified for `zh-Hans` and Traditional for `zh-Hant`,
  with each region's vocabulary (*软件* / *軟體*, *默认* / *預設*), not a
  character conversion of one into the other.
- **Japanese, Chinese, Korean**: no spaces between words; full-width
  punctuation (`。`, `、`, `！`, `？`, `：`). Keep half-width digits and units.
- **Arabic and Hebrew**: right to left. Placeholders and Latin words inside the
  sentence keep their own direction, so check a line such as
  `الوقت: %@` in the app, not just in the file.
- **Quotation marks**: German „…“, French « … » with spaces, Spanish « »
  or “…”, Japanese 「…」, English “…”. Follow the existing translations.

## Regional variants

Treat these as separate languages when the catalog has both:

- `es` (Spain) and `es-419` (Latin America): *ordenador* / *computadora*,
  *vosotros* is absent in `es-419`. In `es` alone, the Spain forms are right.
- `pt-BR` and `pt-PT`: *celular* / *telemóvel*, *você* / *tu*.
- `en-GB`, `en-AU`: spelling (*colour*) and some terms.
- `fr` and `fr-CA`: mostly shared, differences in units and a few words.

## Names that look like words

A key whose English text is a game or app term (*Level*, *Score*, *Blitz*) may
be best left as is in some languages and not in others. When unsure, check what
the same thing is called in the system apps and games of that language, and
note the decision in the `comment` so the next translator keeps it.

## After writing

Run `apply` without `--write` and read the list once as a user would. The
things worth a second look: a key that came out the same as the English; a
sentence whose subject changed (*he*, *she*, *it*) with a variable; and any
text that mentions another key's text (*Tap "Read the tag"*), which must use
the same wording as that key's translation.
