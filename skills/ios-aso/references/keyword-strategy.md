# Keyword strategy

How to decide what goes in the name, the subtitle and the keyword field, per
localization. The counting is done by `scripts/keyword_pack.py` and
`scripts/aso_audit.py`; the judgement is yours and the user's.

## The four rules that cost people the most

1. **Each word is worth once.** A word in the name is already indexed; putting
   it in the subtitle or the keyword field spends characters for nothing. The
   same holds across localizations that feed one storefront.
2. **Commas, no spaces.** `clock,morning,habit`, not `clock, morning, habit`.
   Every space is a lost character.
3. **No plurals of a singular you already have, no category name, no "app".**
   Apple's guidance names all three.
4. **Only what is true and relevant.** No competitor names, trademarks,
   celebrity names, prices, or terms that do not describe the app. This is
   an App Review rejection reason, not only a style point.

## Process

### 1. Write down what the app is, in the user's words

Not the feature list: the problems people have and the words they would type.
Collect them from the app's own reviews and support mail, the user's own
description of who it is for, and the language of competing listings. A
keyword nobody types is worth nothing however well it is packed.

### 2. Build a candidate list

For each localization you are optimizing, 30 to 60 candidates is plenty:

- the job or problem ("wake up", "oversleep", "focus")
- the mechanism or object ("qr", "nfc", "steps", "widget")
- the audience ("student", "shift worker")
- synonyms and the other common words for the same thing in that language

Write candidates **in the language of that localization**, as a native speaker
would search. A translation of the English list is usually the wrong list.

### 3. Rank them

This is the part software cannot do for you. Rank by, in this order:

1. **Relevance**: would a person typing it want this app? If not, drop it.
2. **Intent fit**: someone typing it is looking for what you do, not for
   something adjacent.
3. **Competition and popularity**, only if you have data. Apple Ads
   (previously Search Ads) shows relative popularity for keywords. Without
   such data, prefer specific two-or-three-word intents over single generic
   words that every large app competes for.

State clearly when a ranking is judgement and not data.

### 4. Place words across the fields

- **Name (30):** the brand plus, if it fits naturally, the one phrase that says
  what the app does. A name that reads as a keyword list hurts conversion and
  can be rejected.
- **Subtitle (30):** a human sentence that adds *new* words, not the name's.
  It is read by people, so it must also sell.
- **Keywords (100):** everything else, most important first, no repeats of the
  name or subtitle.

### 5. Pack, and look at what was skipped

```bash
python3 scripts/keyword_pack.py \
    --candidates "clock,morning,heavy sleeper,oversleep,qr,nfc,steps,math,shake,habit" \
    --name "Brand: Alarm Challenge" --subtitle "Wake up on the first try"
```

The order of `--candidates` is the priority. The output lists what was skipped
and why (already indexed, singular/plural) and what did not fit. Read the
skipped list: it often shows a candidate you ranked highly that the name
already covers.

## Using more than one localization

Each added localization is another keyword field, and several can feed the
same storefront (see `how-search-works.md`). Use that deliberately.

- **Do not repeat.** When you pack the second localization of a storefront, pass
  the first one's text as `--also-indexed` so the packer skips words already
  spent there.
- **Do not copy one localization into another.** Two identical keyword fields
  feeding one storefront are one field. This most often happens with es-ES and
  es-MX, or en-US and en-GB.
- **A localization is also a page.** Its description, screenshots and promotional
  text are what that audience reads. Add one only if you can write real text
  for it; otherwise the extra keyword space costs conversion.
- **Using a secondary localization's field for another language's words** (for
  example English keywords in an es-MX field to widen the US storefront) is a
  known industry tactic. It works against the field's own market, so apply it
  only where that market is unimportant to you, and keep the words truthful.

## Limits of this approach

- A packed field is only as good as the ranking you fed it.
- Indexing takes time. Practitioners commonly wait one to two weeks after a
  version goes live before judging (a rule of thumb, not an Apple figure), and
  change one set of things at a time so an effect can be attributed.
- Changing keywords, name or subtitle needs a **new version**. Only promotional
  text can change without one.
