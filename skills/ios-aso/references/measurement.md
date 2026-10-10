# Measuring whether it worked

ASO changes are slow, noisy and easy to over-read. This is how to keep the
conclusions honest.

## Where the numbers come from

| Need | Source | Notes |
|------|--------|-------|
| Impressions, product page views, conversion rate, downloads, redownloads, retention | App Analytics in App Store Connect | Apple's own; includes a breakdown by how people found the app and a comparison of custom product pages with the default page |
| Result of an icon/screenshot/preview test | Product page optimization results | Reports whether a treatment beats the baseline with at least 90% confidence |
| Keyword popularity | Apple Ads (previously Search Ads) keyword tools | Relative figures, for the keywords you can plan campaigns around |
| Where you rank for a term | Searching on a device, in the storefront and language you care about | Free, manual, and the only ground truth. Log the date |
| Search volume and competitor rankings at scale | Third-party ASO tools | Paid and estimated; this skill does not provide them |

Programmatic access to analytics reports is possible through the App Store
Connect API, but it can need a key with a higher role than the one used to
publish; a `403` there is a permissions matter, not a bug.

## Before you change anything

Write down, with the date:

- the current name, subtitle and keywords of each localization you will touch
- impressions, product page views and conversion for the last 4 weeks
- for a handful of target terms, where the app appears in search today

That is the baseline. Without it every later number is an anecdote.

## When you change something

- Change one *set* of things per version (for example: keywords in two
  localizations), so a movement can be attributed to it.
- Remember what needs a new version (name, subtitle, keywords) and what does
  not (promotional text).
- Allow indexing time before judging; one to two weeks is the common rule of
  thumb among practitioners, not an Apple figure.

## Reading the result

Separate the two questions:

- **Did more people see the app?** Impressions, and rank for the target terms.
  This is about the name, subtitle, keywords and category.
- **Did more of those who saw it install?** Conversion rate. This is about the
  screenshots, preview, icon, rating and copy.

A change that raises impressions and lowers conversion (broader keywords
bringing in the wrong audience) is common and is a bad trade; look at both.

Small apps have small numbers. A week with a few dozen page views cannot
distinguish a good change from a good week. Say what the data can and cannot
support, and prefer fewer, larger changes over many small ones.
