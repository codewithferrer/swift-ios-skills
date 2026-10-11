# Where an iOS app needs a record

The catalogue of places to look when deciding whether an app can be
understood from its data. For each: what to record, the pitfall that makes the
data wrong, and how the scan sees it.

## The test for a gap

A missing record is worth closing when **all** hold:

1. A decision depends on the answer (see `measurement-planning.md`).
2. The app cannot answer it from other events (the settings may already ride
   on `game_start`; the path is already in `screen_view`).
3. It will happen often enough, or matter enough when it does, to read.

If one fails, leave it unrecorded. Over-instrumenting is a cost: parameter
slots, noise in every report, and a larger privacy surface.

## Lifecycle

| Point | Record | Pitfall |
|-------|--------|---------|
| First launch | Nothing: `first_open` is automatic | Logging it again conflicts with the platform's |
| Cold start | Rarely needed: `session_start` is automatic | An "app opened" event from `onAppear` of the root fires on every return from background in some setups |
| Foreground | Only to attribute a session to what opened it | See entry points below |

## Screens

- **SwiftUI automatic reporting names screens after hosting controllers.** To
  get meaningful names, log `screen_view` yourself with `screen_name` and
  switch automatic reporting off (`FirebaseAutomaticScreenReportingEnabled`
  = NO). Both on counts each screen twice.
- Where to log it: in the navigation layer when there is one (a router), so
  one place covers every destination; otherwise in the destination's
  `onAppear`.
- **`onAppear` fires again** when a sheet over the view is dismissed, when a
  tab is reselected, and when a navigation pops back. If the same screen can
  be re-shown inside one visit, guard with state or log at the navigation
  call.
- Sheets, full screen covers and alerts are screens people see. A sheet that
  holds a decision (difficulty, clock, share) is worth a view; a confirmation
  alert usually is not.
- `coverage` lists presented views that have no `screen_view` and no event
  behind them. The root (`WindowGroup`) is usually a container: look at the
  real home view.

## The core loop

The scan's *core loop* category is any control whose label or callee says
start, play, finish, submit, retry, quit, skip, hint.

- Record the **start** with every setting that changes the experience as a
  parameter (mode, difficulty, theme, clock), plus the count of sessions in
  this run (`session_number`), which is the best engagement signal you can
  get from one app launch.
- Record **every end**: completed, failed with a bounded reason, quit.
- **Abandonment has no tap.** If the app is backgrounded or closed in the
  middle of a loop there is no end event. Either treat a start with no end as
  abandoned in analysis, or log from `scenePhase` changes while a loop is
  active (`loop_interrupted`).
- Hints, undos, skips and retries are cheap events that say where the
  difficulty is. Count them as parameters of the end event
  (`hints_used`, `retries`) rather than one event each.

## Money

- Paywall: shown (with `source`: which entry point), started, and each outcome
  (completed, cancelled, failed with a bounded reason), restore with its
  result.
- **Log where the money flow is decided, not where the SDK is called.** The
  service layer (`PurchaseManager.purchase`) usually should not log; the view
  that knows `source` and the product should. The scan treats a purchase site
  as covered when the function or a caller logs, and says so.
- **Failure reasons must be bounded.** `error.localizedDescription` is free
  text: it varies by language, can carry identifiers, and is cut at 100
  characters. Send the error's domain and code, or a mapped enum
  (`cancelled`, `network`, `not_allowed`, `product_unavailable`, `unknown`).
- The platform records the completed purchase itself (`in_app_purchase`) and
  subscription renewals and conversions. RevenueCat and App Store Connect
  report revenue authoritatively; analytics events are for the funnel.

## Permissions

`requestAuthorization` and its siblings (camera, motion, notifications,
location, health, alarms) are where first-run people drop. Record the prompt
and the result:

```swift
func requestNotifications() {
    UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
        AnalyticsManager.shared.permissionResult(kind: "notifications", granted: granted)
    }
}
```

- The system prompt can be shown only once. If a pre-prompt screen explains
  why, record it too: "pre-prompt shown" to "prompt granted" is the real
  funnel.
- A denied state later (a person who reads a banner and opens Settings) is
  worth a record where the app detects it.

## Entry points that are not the main UI

Where a session came from changes how to read it.

| Entry | Where to see it | Pitfall |
|-------|-----------------|---------|
| Local or remote notification opened | `userNotificationCenter(_:didReceive:...)` | The tap launches or foregrounds the app, so record the `source` before the first screen |
| Widget tap, Live Activity | The URL or intent that opens the app (`onOpenURL`) | A widget extension is another process; the SDK is usually not there |
| App Intent, Siri, Shortcuts | `perform()` of the intent | May run without the app's UI or without Firebase configured; check whether the process has the SDK, and if not, record a counter and send it at next launch |
| Deep link, universal link | `onOpenURL`, `onContinueUserActivity` | Record the source category, never the URL's query |
| Share sheet, Share extension | The completion handler | The completion value says completed or cancelled and the activity type |

`coverage` lists App Intents and permission prompts that have no record, plus
notification and URL handlers are worth a manual look.

## Errors

- A `catch` that only prints is invisible in production. The scan lists them,
  with the ones inside purchase, permission and destructive code first.
- Record the **kind** of failure and where, never the message. Crash reporting
  (Crashlytics) is for crashes and `record(error:)`; analytics is for the
  funnel impact ("how many purchases fail", not "why").
- Do not record every transient network retry. Record the final failure a
  person felt.

## Preferences

Record the value in force on the loop events, as above. A "setting changed"
event earns its place only when the change is itself the question ("do people
turn the clock off?"). Controls that open a picker are covered when the
selection reaches an event, through `onChange` or a property observer; the
scan follows both.

## Destructive actions

Delete, reset, clear, sign out. Record that it happened and a count (items
removed), not what. These are rare, so they are cheap, and a spike is a signal
(a bug, or a confusing layout).

## Verifying

- **DebugView** shows events from a development device within seconds; launch
  with the argument in `firebase-ga4.md`. Check the event, every parameter and
  the parameter *type* (a bool arrives as 1 or 0).
- **A test on the facade** is the cheapest guard: inject the handler, call each
  method, assert the name is valid and within limits and the parameters are
  the planned ones. The `check` command covers the static half of this.
- **Realtime and standard reports lag**: standard reports take hours, and a
  newly registered custom dimension reports 24 to 48 hours later.
