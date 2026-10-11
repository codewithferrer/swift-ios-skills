# Planning what to measure

A measurement plan is the list of questions the product needs answered, and
the smallest set of events that answers them. Writing the questions first is
what keeps the event list short and useful.

## The method

1. **List the decisions.** What will someone do differently depending on the
   data? "Whether to keep building daily challenges", "whether the paywall
   copy works", "which difficulty to default to".
2. **Turn each into a question a number can answer.** "What share of people
   who see the paywall buy?" "Of people who start a puzzle, how many finish?"
3. **Name the events and parameters that make the numerator and the
   denominator.** Most questions need two events (a start and an outcome) and
   a parameter that splits them (mode, source, difficulty).
4. **Drop every event that answers no question.** It costs a dimension slot,
   a review, and a place to be wrong.
5. **Write it down** as a table next to the product document, with the
   question in a column. A plan that is only in the code cannot be reviewed.

```
| Event | Parameters | Question it answers |
|---|---|---|
| `paywall_shown` | `source` | Which entry point sends people to the paywall? |
| `purchase_completed` | `product_id` | What share of views convert, per product? |
```

The scan reads exactly this: a table whose first column is `Event`, names in
backticks, parameters in the second column.

## Questions every app has

| Area | Question | Needs |
|------|----------|-------|
| Activation | Do people reach the first moment of value, and how long does it take? | One event for that moment, the onboarding steps before it |
| Core loop | How many sessions of the main thing, and how do they end? | Start and outcome events with the settings that were in force |
| Retention | Do they come back, and what brought them back? | A `source` for every entry (notification, widget, intent), first and later opens |
| Monetization | Where do people see the offer, and what do they do? | The whole funnel, every branch |
| Friction | Where do people get stuck or fail? | Failures with a bounded reason; permission results |
| Preferences | What do people choose, and does it change how they play? | Settings as parameters of the loop events |

## Funnel templates

A funnel is a list of events where each is a subset of the one before. Every
step needs an event, and **every way out of the last step needs one too**.

**Paywall.** `paywall_shown(source)` -> `purchase_started(product_id)` -> one
of `purchase_completed`, `purchase_cancelled`, `purchase_failed(reason)`;
separately `purchase_restored(success)`. If the SDK logs the success itself
(Firebase's `in_app_purchase`), do not log it twice; log the branches it does
not cover.

**Onboarding and permissions.** `onboarding_step(step)` ->
`onboarding_completed` or `onboarding_skipped`; for each permission:
`permission_prompted(kind)` -> `permission_result(kind, granted)`. The
pre-prompt screen is a funnel step of its own.

**A game or a challenge.** `session_started(mode, difficulty)` ->
`session_ended(result, duration_bucket)` with `result` in won, lost, quit;
progress events (`level_end`) inside. The loss reason is a parameter of the
end event, not a separate event per reason.

**A habit or utility.** `item_created` -> `item_triggered` (the alarm rang) ->
`item_resolved(how, seconds_to_resolve)`. The gap between triggered and
resolved is the product's real quality.

## Designing events

- **One event, one meaning.** `game_start` with `game_mode` beats
  `classic_game_start`, `fog_game_start`, `puzzle_game_start`: the same
  question needs one report, not three. The event count also has a cap.
- **Name the thing and what happened**, past tense for outcomes:
  `alarm_created`, `challenge_failed`. Lowercase, underscores, at most 40
  characters, and not a name Google reserves.
- **Prefer Google's recommended names** where one fits (`level_start`,
  `level_end`, `tutorial_begin`, `tutorial_complete`, `unlock_achievement`,
  `post_score`, `share`, `purchase`): the built-in reports and audiences use
  them.
- **Pair starts with ends.** An event with no counterpart cannot give a rate
  or a duration.

## Designing parameters

- **Bounded.** A parameter is a dimension: every distinct value is a row. Use
  an enum, a small fixed vocabulary, or a bucket (`seconds_bucket` of `0-5`,
  `5-15`, `15-60`, `60+`) instead of a raw number when you will group by it.
  Google treats a dimension with more than 500 values as high-cardinality,
  and in practice its long tail is grouped as "(other)".
- **Typed once.** `success` is a bool in every event, or an int in every
  event; a custom dimension has one type.
- **One name per idea.** `source`, not `source` here and `origin` there;
  `product_id`, not `product_id`, `package_id` and `sku`.
- **Never user text, names or identifiers**: see `privacy.md`.
- **Put the context on the event that needs it**, not on a separate "settings
  changed" event, unless the change itself is the decision signal. The
  difficulty and theme in force belong on `game_start`.
- **Mind the budget**: a standard property has 50 event-scoped custom
  dimension slots, shared by every app that sends to it. Parameters you will
  never group or filter by do not need one; they are still available in
  DebugView and BigQuery.

## Sizing

A small app needs 12 to 30 events. If the inventory has more than 50, look for
families that should be one event with a parameter. If it has fewer than 10,
look at the funnels above for a missing branch.

## What not to record

- Taps on controls that only navigate or dismiss (screen tracking has the path).
- Anything that restates what the platform logs (`first_open`, `session_start`,
  `in_app_purchase`, `app_update`).
- State you can derive from two events you already have.
- Values nobody will ever filter on.
- Anything a person typed, anything that identifies them, anything about
  other people.
