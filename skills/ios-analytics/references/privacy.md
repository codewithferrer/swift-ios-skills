# Privacy and analytics

Analytics should describe behaviour, not people. This file is what to check
before shipping an event, a user property or an SDK. It is engineering
guidance, not legal advice; where a regulation applies, a lawyer decides.

## What never goes into an event

- Text a person typed (names of things they created, search terms, notes).
- Names, email, phone, address, precise location, birth date.
- Free-form error messages; they carry paths, names and identifiers.
- A user ID, unless you have a real need and consent, and then only a
  pseudonymous one you generate yourself (never an email or an account name).
- Anything about other people (contacts, calendar attendees).

`check` flags parameter names that sound personal, values built from
interpolation or descriptions, and any `setUserID`. It cannot see what a
variable holds at runtime, so a computed value (`palette.name`) is reported for
you to confirm.

## User properties are for buckets

A user property describes a bucket the person belongs to (plan: free or
pro, language, the number of alarms in a band), never an identity. It stays
attached to every later event, so a wrong value pollutes the whole history.

## Identifiers

| Identifier | What it is | Note |
|------------|------------|------|
| IDFA | Advertising identifier | Reading it needs the user's permission through App Tracking Transparency |
| IDFV | Vendor identifier, per developer account | Firebase Analytics collects it unless `GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED` is NO |
| App instance ID | Firebase's own per-install ID | Resets on reinstall; the basis of Firebase's user counts |

The Firebase package ships the identity-support module as a separate library.
An app that does not need ad attribution or ad audiences can link
`FirebaseAnalyticsCore` and leave it out.

## App Tracking Transparency

Apple defines *tracking* as linking data from your app with data from other
companies' apps, websites or offline properties for targeted advertising or
advertising measurement, or sharing it with data brokers. Analytics that stay
within your own app and are not combined with other companies' data are not
tracking in that sense, and an app that does no tracking should not show the
ATT prompt or import `AppTrackingTransparency`. `check` lists the import so
you can confirm it is needed.

## App Privacy details and the privacy manifest

Two things Apple asks of an app that collects data:

1. The **App Privacy** answers in App Store Connect: which data types you
   collect, what for (analytics, app functionality, ...), and whether they are
   linked to the person. Analytics events about product interaction are
   declared as *Product Interaction*, purpose *Analytics*; identifiers such as
   the device ID are declared when collected. The answers must match what the
   SDKs actually do.
2. A **privacy manifest** (`PrivacyInfo.xcprivacy`) in the app target. Since 1
   May 2024, uploading an app or an update that uses a *required-reason API*
   without an approved reason in its manifest is flagged by App Store Connect
   (ITMS-91053). `UserDefaults` is the most common one. Third-party SDKs
   bring their own manifests; the app's must cover the app's own code and
   list its collected data types.

`check` reports a project that links an analytics SDK and contains no
`PrivacyInfo.xcprivacy` under its folder.

## Consent

Whether analytics need prior consent depends on where the people are and what
identifiers are involved; in the EU an app instance ID or an advertising
identifier is generally treated as personal data. Firebase's consent mode and
`Analytics.setAnalyticsCollectionEnabled(_:)` give the controls: collect
nothing until the person agrees, or collect nothing that identifies. Decide it
for the whole app, write it in the privacy policy, and make the in-app switch
actually call `setAnalyticsCollectionEnabled`.

Apps for children carry stricter rules in every store; do not add third-party
analytics to one without reading them.

## Sources

- Apple, User Privacy and Data Use (App Tracking Transparency): https://developer.apple.com/app-store/user-privacy-and-data-use/
- Apple, Privacy manifest files: https://developer.apple.com/documentation/bundleresources/privacy-manifest-files
- Apple Developer News, Privacy updates for App Store submissions: https://developer.apple.com/news/?id=r1henawx
- Google, Configure analytics data collection: https://firebase.google.com/docs/analytics/configure-data-collection
