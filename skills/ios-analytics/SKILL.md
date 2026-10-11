---
name: ios-analytics
description: Understand, audit and extend the analytics of an iOS app. Reads the Swift code to inventory the events it records, finds the screens, controls, errors, permission prompts and purchases that leave no trace, checks names, parameter cardinality, privacy and Firebase/GA4 limits, compares the code with an event plan, and works out where to instrument and what to record so the app's real behaviour (funnels, retention, monetization, failures) can be answered. Use whenever the user wants to add, review or plan analytics, events, funnels or tracking in an iOS app, asks where tracking is missing, what to measure, why a report is empty or wrong, or how to verify events in DebugView, even if they never say analytics.
compatibility: Python 3.9+. Reads the project's Swift sources and Info.plist only; no network, no credentials, and nothing is written.
---

# iOS analytics

Analytics exist to answer decisions: which feature to build next, where people
drop, whether the paywall earns, whether a release broke something. An app
that logs fifty events and cannot answer those is blind in the places that
matter; an app that logs fifteen well-chosen ones is not. This skill does two
things: it **reads the code** to show what is recorded and what is not, and it
gives the **judgement** for deciding what deserves a record.

## The commands

All read-only. Run from the project folder or pass `--root`; `--exclude TEXT`
skips paths (a watch app, debug screens); `--json` for scripts; `--all`
lists everything.

| Command | Answers |
|---------|---------|
| `analytics_scan.py inventory` | What events exist, their parameters, who sends them, what values callers pass |
| `analytics_scan.py coverage` | Which screens, controls, permission prompts, purchases, intents and caught errors leave no record |
| `analytics_scan.py check [--plan FILE]` | What is wrong: names, limits, cardinality, personal data, double counting, configuration |
| `analytics_scan.py plan --plan FILE` | The event plan against the code, and where each missing event would go |

It understands a typed facade (`AnalyticsManager.shared.gameStart(...)`) over a
private `log(_:_:)` over `Analytics.logEvent`, direct SDK calls, and typed
event enums; Firebase, TelemetryDeck, PostHog, Mixpanel, Amplitude and Aptabase
call shapes. Resolution is by name, so every finding carries a `file:line` to
open: treat results as strong leads, not proof.

## Working through an app

1. **Inventory first.** `inventory` shows the vocabulary the app already
   speaks. Read it before proposing anything: new events must use the names
   and parameter vocabulary that exist, or reports split in two.
2. **Then coverage.** Read the *high-priority* list and the entry points. A
   gap is only worth closing if a decision hangs on it; `references/where-to-instrument.md`
   has the test and the catalogue of places an iOS app needs a record.
3. **Then check.** Errors are bugs in the data (a reserved name is dropped, a
   dynamic name blows the event cap). Warnings are reports that will mislead.
4. **Decide from questions, not from the list.** Write the three to seven
   questions the product needs answered, then the events that answer them;
   `references/measurement-planning.md` has the method and funnel templates.
   Put the result in the project's plan document as a table (`Event` and
   `Parameters` columns, names in backticks); `plan` and `check --plan` read it.
5. **Implement in the project's own style**: the facade method, the call at the
   moment of truth, the plan row. Then `check` again, and verify live (below).

## Adding an event

1. Is there an existing event that, with a parameter, answers it? Prefer
   one event with a bounded parameter over many events.
2. Name it `object_action` in snake_case, past tense for outcomes (`alarm_created`),
   within 40 characters, not reserved (`check` knows the list). Reuse a Google
   recommended name when one fits (`level_end`, `tutorial_complete`): the
   built-in reports light up.
3. Add the method to the facade, with typed parameters and **bounded** values:
   an enum's analytics name, a number, a bool, a fixed source string. Never
   free text, a user-chosen name, or `error.localizedDescription`.
4. Call it at the moment the thing *happened* (after the save succeeded, in
   the completion, in the `didSet` of the setting), not at the tap. Never in
   `body` or `init`, which SwiftUI runs repeatedly.
5. Add the row to the plan table; run `check --plan` and `coverage`.
6. Verify in DebugView, then register new parameters as custom dimensions
   (they report only after that, and 24-48 hours later).

## Deciding where a record is worth having

Rank gaps by the cost of not knowing:

- **Money**: paywall shown, started, succeeded, cancelled, failed, restored.
  A funnel with a missing branch cannot be read.
- **First run**: onboarding steps and permission prompts with their result.
  Activation is decided here, and so is most churn.
- **The core loop**: start and every way it ends (completed, failed,
  abandoned), with the settings in force as parameters of the start event.
- **Failure**: errors a person felt (purchase failed, sync failed, a challenge
  that cannot be completed). A `catch` that only prints is invisible.
- **Entry points other than the UI**: notifications opened, widgets, App
  Intents, deep links. Where a session *came from* explains its behaviour.
- **Everything else is optional.** Close, back and cosmetic toggles rarely
  change a decision; screen tracking already shows the path.

## Gotchas

- **An unregistered parameter is invisible in reports.** It shows in DebugView
  and in the BigQuery export, but not in standard reports or explorations until
  it is registered as a custom dimension; a property has 50 event-scoped slots,
  shared by every app that sends to it.
- **Automatic screen reporting in SwiftUI** names screens after hosting
  controllers. Either log `screen_view` by hand and turn the automatic one off
  (`FirebaseAutomaticScreenReportingEnabled` = NO), or accept the noise. Doing
  both counts every screen twice; `check` flags it.
- **Extensions are other processes.** A widget or an App Intent run by the
  system may not have the SDK linked or initialized; record what happened from
  the app side when it next runs, or pass a counter through an app group.
- **Abandonment has no event.** Nobody taps "I gave up". Record the start, and
  treat a start with no end event as the abandonment, or log from
  `scenePhase` changes while a loop is active.
- **Static reading has limits.** It cannot see values at runtime, closures a
  parent passes in (reported as *delegated*), code generated by macros, or
  Objective-C. A control it calls uncovered may be recorded by its parent.

## Reference files

| File | Read it when |
|------|--------------|
| `references/measurement-planning.md` | Deciding what to measure; building a plan, funnels, event and parameter design |
| `references/where-to-instrument.md` | Judging a gap; the catalogue of instrumentation points in an iOS app and their pitfalls |
| `references/firebase-ga4.md` | Limits, reserved names, automatic and recommended events, custom dimensions, DebugView |
| `references/privacy.md` | Anything touching identifiers, consent, App Privacy details or the privacy manifest |
