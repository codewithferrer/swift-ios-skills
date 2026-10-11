# How App Store search and product pages work

What is known, how well it is known, and where it comes from. Read this before
giving advice that depends on "what Apple indexes", because the sources differ
in how much they can be trusted.

Every claim carries one of three labels:

- **Apple states** - written on an Apple page (linked at the end).
- **Observed** - checked against real data when this skill was written. It is
  how the world behaved then; re-check it before leaning on it.
- **Industry reports** - widely repeated by ASO practitioners and tool vendors,
  but not stated by Apple. Useful, not guaranteed.

## What drives search results

| Claim | Label |
|-------|-------|
| Results depend on text relevance (app name, subtitle, keywords, primary category) and on user behaviour (downloads, ratings and reviews, more) | Apple states |
| Promotional text does not affect search ranking | Apple states |
| The description is not among the text-relevance factors Apple lists, and Apple advises against adding keywords to it for SEO | Apple states (by omission and by advice) |
| Screenshots (the first few), previews, icon, description, in-app events and promoted in-app purchases are *discoverable elements* of a result | Apple states (it does not say they affect ranking) |
| Text inside screenshots is read and used for ranking since mid-2025 | Industry reports |
| Each word counts once across name, subtitle and keywords, and Apple combines words across those fields into phrases | Industry reports |
| In-app purchase names can surface in search | Apple states (promoted purchases); wider claims are industry reports |

Practical reading: **name, subtitle and keywords are the three fields you
write for search; the description and promotional text are for the person
already looking at your page.** Spend search effort on the first three.

## The character budgets

| Field | Limit | Label |
|-------|-------|-------|
| Name | 30 characters | Apple states |
| Subtitle | 30 characters | Apple states |
| Keywords | 100 characters, comma-separated, no spaces between terms | Apple states |
| Promotional text | 170 characters, editable without a new version | Apple states |
| Description | 4000 characters | Apple states |

### Keywords: characters or bytes?

Apple's own pages disagree. The product page guidance says 100 *characters*;
the App Store Connect help for version information says 100 *bytes*. For Latin
text they are the same. For Japanese, Korean, Chinese and Arabic they are not:
one character takes two or three UTF-8 bytes.

**Observed:** App Store Connect accepted live keyword fields with more than 100
UTF-8 bytes but never more than 100 characters, so the limit it *enforces* is
characters. Plan to 100 characters.

**Not settled:** whether text past the 100th byte of such a field is *indexed*.
You can find out: put a distinctive, otherwise irrelevant word at the very end
of a long non-Latin field, ship it, and after indexing settles check whether the
app appears for that word in that storefront's search. Until then treat the
tail as possibly ignored, and put the words that matter most first.

## Which localizations feed which storefront

Apple publishes, for every storefront, a default localization and a list of
additional localizations it supports (the App Store localizations page). A
few rows, as published:

| Storefront | Default | Additional |
|------------|---------|------------|
| United States | en-US | ar-SA, zh-Hans, zh-Hant, fr-FR, ko, pt-BR, ru, es-MX, vi |
| Spain | es-ES | ca, en-GB |
| Mexico | es-MX | en-GB |
| Germany, France, Italy, Brazil, Netherlands | local language | en-GB |
| Argentina, Colombia, Chile, Peru (and most of Latin America) | **es-MX** | en-GB |
| Austria | de-DE | en-GB |
| Switzerland | de-DE | en-GB, fr-FR, it |
| United Kingdom, Ireland | **en-GB** | none |
| United Arab Emirates | **en-GB** | ar-SA |
| Egypt | **en-GB** | ar-SA, fr-FR |
| Singapore | **en-GB** | zh-Hans |
| Belgium | **en-GB** | nl-NL, fr-FR |
| Taiwan, Hong Kong | zh-Hant | en-GB |
| Japan | ja | en-US |

The full set is in `references/storefront-locales.json` (a subset: extend it
from Apple's page).

**Apple states** the table. **Industry reports** that the keywords of those
additional localizations are also searched in that storefront, which is why one
storefront can be worth several keyword fields. Apple's page describes them as
supported languages and does not spell out the search effect, so treat the
multiplication of keyword space as likely, not certain.

Two consequences:

1. One localization can serve several storefronts. **es-MX is the default of
   most of Latin America**, so a Mexican Spanish listing is the Spanish listing
   for Argentina, Colombia, Chile and Peru as well, and it is worth writing
   for all of them, not only for Mexico. **en-GB is the default of six
   storefronts** (the United Kingdom, Ireland, the United Arab Emirates, Egypt,
   Singapore and Belgium) and an additional language for most non-English
   ones, so without an en-GB localization those markets fall back to
   whichever English you have.
2. A word is wasted if it appears in two localizations that feed the same
   storefront. `scripts/aso_audit.py` finds those.

## What you cannot see

- **Search volume and competitor rankings.** Apple does not publish them in
  the listing. Third-party tools estimate them, and this skill does not.
- **A competitor's keyword field.** It is hidden. You can read their name,
  subtitle, screenshots and description, and infer from those.
- **Why a single ranking moved.** Ranking mixes relevance and behaviour; one
  change rarely explains one movement.

## Sources

- Apple, App Store search: https://developer.apple.com/app-store/search/
- Apple, product page: https://developer.apple.com/app-store/product-page/
- Apple, App Store localizations (App Store Connect Help):
  https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations/
- Apple, platform version information (App Store Connect Help):
  https://developer.apple.com/help/app-store-connect/reference/app-information/platform-version-information/
