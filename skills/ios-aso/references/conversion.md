# Conversion: turning a product page view into a download

Search gets someone to your page. These are the things on the page that decide
whether they install. Figures quoted here are Apple's, from the pages linked at
the end; anything else is labelled.

## What a person sees first

In search results, the first one to three screenshots (or the preview, when
there is one) sit next to the name, subtitle and icon. Most people decide from
that, so the first screenshots carry most of the weight.

### Screenshots

- Up to 10 per localization. **Apple states** the first 1 to 3 appear in search
  results.
- Give each screenshot one job: one benefit or one feature, readable in a
  second. A caption of a few words, large, beats a paragraph.
- Show the app in use. Splash screens, login screens and device frames with no
  UI are rejection triggers (App Review Guidelines 2.3).
- Include at least one Dark Mode screenshot if the app supports it
  (**Apple states** this as guidance).
- Text in screenshots may also be read for search (**industry reports**), so
  write captions that name what the app does, in that localization's language.
- The primary localization needs its own set; other localizations fall back to
  it when they have none. Localize the first three before the rest.

### App preview video

- Up to 3 previews, 30 seconds each at most; they autoplay muted
  (**Apple states**).
- The first seconds matter most: assume it is watched without sound and often
  not to the end.
- It must show the app itself, not unrelated footage.

### Icon

Simple, recognizable at small sizes, and distinct from neighbours in the same
category. Apple's guidance is to avoid unnecessary detail. It is also the one
asset that appears everywhere, so test it (below) before redrawing it.

### Name and subtitle as copy

They are search fields *and* the first words a person reads. A subtitle that
is only keywords reads as spam; one that says what you get and for whom
converts and still indexes.

### Description and promotional text

- The first sentence is read without tapping "more"; Apple calls it the most
  critical. Lead with what the app does and for whom.
- Concise paragraph, then a short feature list. No prices (they are already
  on the page and vary by region).
- Promotional text (170 characters) sits above the description and can change
  at any time **without a new version**. Use it for what is true now: an
  update, a seasonal angle, an event. It does not affect search ranking.

### Ratings and reviews

- Ratings are shown in search results and are one of the behaviour signals
  Apple lists. A summary rating is specific to each territory.
- The system rating prompt can be shown **at most three times in 365 days**
  (**Apple states**), so spend it after a moment of satisfaction (a level
  finished, a task done), never mid-task or on launch.
- Replies to reviews are public. Keep them short, specific and polite, and
  never ask a reviewer to change their rating.

## Testing and variants (Apple's own tools)

| Tool | What it does | Apple states |
|------|--------------|--------------|
| Product page optimization | A/B test icon, screenshots or preview against the current page | Up to 3 treatments, one test at a time, up to 90 days, localizable, results with 90% confidence |
| Custom product pages | Extra versions of the page (screenshots, promotional text, previews) for a specific audience or campaign | Up to 70; can appear in search results when keywords are assigned (each keyword combination to one page); reviewed independently of app updates |
| In-app events | Time-limited events surfaced in search and on the page | Up to 10 published, 15 approved at once; up to 31 days; name 30, short description 50, long description 120 characters |

Notes that save time:

- You cannot test the name, subtitle, description or keywords with product
  page optimization; those are only changed by shipping them and comparing.
- A test needs traffic. An app with few daily page views may not reach
  confidence in 90 days; say so rather than reading noise as a result.
- Alternate icons in a test must be in the shipped binary.
- Custom product pages are worth it when you have a distinct audience or a
  paid campaign to point at them. Otherwise they are maintenance without a
  purpose.
- An in-app event needs real content with a start and an end. A permanent
  feature is not an event.

## What not to do

Anything that misleads: screenshots of features that do not exist, claims you
cannot back up ("#1", "best"), other platforms' names, competitor names, or
pricing in the name or subtitle. These cost a rejection at best and trust at
worst.

## Sources

- Apple, product page: https://developer.apple.com/app-store/product-page/
- Apple, product page optimization:
  https://developer.apple.com/app-store/product-page-optimization/
- Apple, custom product pages:
  https://developer.apple.com/app-store/custom-product-pages/
- Apple, in-app events: https://developer.apple.com/app-store/in-app-events/
- Apple, ratings and reviews:
  https://developer.apple.com/app-store/ratings-and-reviews/
