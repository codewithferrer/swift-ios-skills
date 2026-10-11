# Firebase Analytics and GA4: limits and rules

Facts the scan checks against, with where they come from. Google changes
these occasionally; confirm a number before building a decision on it.

## Limits for app streams

Source: Google Analytics Help, "Collection limits".

| Item | Limit |
|------|-------|
| Event name | 40 characters |
| Distinct event names | 500 for an app data stream |
| Parameters per event | 25 |
| Parameter name | 40 characters |
| Parameter value | 100 characters (longer is cut) |
| User properties | 25 per property |
| User property name | 24 characters |
| User property value | 36 characters |

Names must start with a letter and use only letters, digits and underscores.
They are case sensitive: `Game_Over` and `game_over` are two events.

## Custom dimensions and metrics

Source: Google Analytics Help, "Custom dimensions and metrics".

An event parameter or user property appears in reports and explorations only
after it is registered as a custom dimension (or metric). Data starts to
report 24 to 48 hours after registration, and only for events logged from
then on. A standard property has:

| Scope | Slots |
|-------|-------|
| Event-scoped custom dimensions | 50 |
| User-scoped custom dimensions | 25 |
| Item-scoped custom dimensions | 10 |
| Custom metrics | 50 |

Slots belong to the **property**. Several apps that send to one Firebase
project and one GA4 property share them, which is why a default
`app_name` parameter is worth setting: it keeps the apps apart in every report.

A dimension with more than 500 possible values is high-cardinality in
Google's terms.

## Reserved names

Source: Google Analytics Help, "Reserved names".

**Events** (rejected or dropped): `ad_activeview`, `ad_click`, `ad_exposure`,
`ad_query`, `ad_reward`, `adunit_exposure`, `app_clear_data`, `app_exception`,
`app_install`, `app_remove`, `app_store_refund`, `app_update`, `app_upgrade`,
`dynamic_link_app_open`, `dynamic_link_app_update`, `dynamic_link_first_open`,
`error`, `firebase_campaign`, `firebase_in_app_message_action`,
`firebase_in_app_message_dismiss`, `firebase_in_app_message_impression`,
`first_open`, `first_visit`, `notification_dismiss`, `notification_foreground`,
`notification_open`, `notification_receive`, `notification_send`, `os_update`,
`session_start`, `user_engagement`.

**Parameters**: `cid`, `customer_id`, `customerid`, `dclid`, `gclid`,
`session_id`, `sessionid`, `sfmc_id`, `sid`, `srsltid`, `uid`, `user_id`,
`userid`. Google's list also contains `currency`, which is the standard
parameter of purchase events and is meant to be used there, so the scan does
not flag it.

**Prefixes** for events, parameters and user properties: `_`, `firebase_`,
`ga_`, `google_` (and `gtag.` for events and parameters).

## Collected automatically

Source: Google Analytics Help, "Automatically collected events".

| Event | Platform | Meaning |
|-------|----------|---------|
| `first_open` | iOS, Android | First launch after install |
| `session_start` | iOS, Android | A session begins |
| `user_engagement` | iOS, Android | App in the foreground at least a second |
| `screen_view` | iOS, Android | A screen transition (unless automatic reporting is off) |
| `in_app_purchase` | iOS, Android | A completed in-app purchase |
| `app_update` | iOS, Android | First launch after an update |
| `os_update` | iOS, Android | First launch after an OS update |
| `app_store_subscription_convert` | iOS, Android | A trial converts to paid |
| `app_store_subscription_renew` | iOS, Android | A paid subscription renews |
| `notification_open`, `notification_foreground` | iOS, Android | A Firebase Cloud Messaging notification opened or received in foreground |

Logging one of these yourself double counts or is dropped (most are reserved).

## Recommended events relevant to apps and games

Source: Google Analytics Help, "Recommended events". Using the name gets the
built-in reporting for it.

| All apps | Games |
|----------|-------|
| `login`, `sign_up`, `search`, `select_content`, `share`, `purchase`, `earn_virtual_currency`, `spend_virtual_currency`, `join_group` | `level_start`, `level_end`, `level_up`, `post_score`, `unlock_achievement`, `tutorial_begin`, `tutorial_complete` |

Each has its own recommended parameters in Google's documentation; the Swift
constants are `AnalyticsEvent...` and `AnalyticsParameter...`.

## Configuration worth knowing (iOS)

Source: Firebase documentation, "Configure analytics data collection".

| Setting | Effect |
|---------|--------|
| `Analytics.setAnalyticsCollectionEnabled(_:)` | Turns collection on or off; **persists across launches** and overrides the plist keys, so set it in both directions on every launch |
| `FIREBASE_ANALYTICS_COLLECTION_ENABLED` (Info.plist) | NO disables collection until the code turns it on with `setAnalyticsCollectionEnabled(true)` |
| `FIREBASE_ANALYTICS_COLLECTION_DEACTIVATED` | Permanently off; wins over everything else |
| `GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED` | NO stops collecting the vendor identifier (collected unless this is set) |
| `GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_PERSONALIZATION_SIGNALS` | NO turns off ad personalization by default |
| `FirebaseAutomaticScreenReportingEnabled` | NO stops automatic `screen_view` (use it when logging screens by hand) |

Swift Package Manager products: `FirebaseAnalytics` (the full product),
`FirebaseAnalyticsCore` and `FirebaseAnalyticsIdentitySupport`, which the
package lists as separate libraries. An app that wants analytics without the
identity-support module links Core alone.

## DebugView

Source: Firebase documentation, "Debug events".

Add `-FIRDebugEnabled` under *Edit Scheme > Run > Arguments Passed On Launch*
(`-FIRDebugDisabled` turns it off). DebugView then shows the raw events of the
development device within seconds, with parameters and user properties.
Without it, events upload in batches over about an hour. Debug events are
also in the BigQuery export, so filter developer traffic out of analysis.

The older spelling `-FIRAnalyticsDebugEnabled` still appears in many projects
and guides; Firebase's current page documents only `-FIRDebugEnabled`. If
events do not appear in DebugView, try the documented one.

## Sources

- Google Analytics Help, Collection limits: https://support.google.com/analytics/answer/9267744
- Google Analytics Help, Reserved names: https://support.google.com/analytics/answer/13316687
- Google Analytics Help, Automatically collected events: https://support.google.com/analytics/answer/9234069
- Google Analytics Help, Recommended events: https://support.google.com/analytics/answer/9267735
- Google Analytics Help, Custom dimensions and metrics: https://support.google.com/analytics/answer/14240153
- Firebase, Configure analytics data collection: https://firebase.google.com/docs/analytics/configure-data-collection
- Firebase, Debug events: https://firebase.google.com/docs/analytics/debugview
