# Metadata files

`pull_metadata.py` and `push_metadata.py` read and write one UTF-8 text file per
field per locale. The layout is the one fastlane's `deliver` uses, so an
existing fastlane `metadata/` folder works as-is:

```
metadata/
  en-US/
    name.txt  subtitle.txt  privacy_url.txt          app-level
    description.txt  keywords.txt  promotional_text.txt
    support_url.txt  marketing_url.txt                per-version
  es-ES/
    ...
```

| File | Limit | Lives on |
|------|-------|----------|
| `name.txt` | 30 | app info |
| `subtitle.txt` | 30 | app info |
| `privacy_url.txt` | - | app info |
| `description.txt` | 4000 | version |
| `keywords.txt` | 100 | version |
| `promotional_text.txt` | 170 | version |
| `support_url.txt`, `marketing_url.txt` | - | version |

- Folder names are App Store Connect locale codes (`en-US`, `es-ES`, `ja`,
  `zh-Hans`). Other folders (fastlane's `default/`, `screenshots/`) are ignored.
- A trailing newline is not part of the value, and an empty file is ignored
  rather than clearing the field.
- A file named `release_notes.txt` is ignored: "What's New" comes from
  `RELEASE_NOTES.md` through `push_whats_new.py`.
- The limits are character limits and are used only to reject text that is
  certainly too long. Anything else Apple dislikes is reported by the API.
- Promotional text is the one field Apple lets you change on a live version
  in the web UI. Whether the API accepts that on a live version has not been
  verified here, so try it with `--dry-run` and a single locale first.

## Version-level files: copyright and App Review information

Two things belong to the version, not to a locale, so they live at the top of
the metadata folder (the same layout fastlane uses):

```
metadata/
  copyright.txt
  review_information/
    first_name.txt  last_name.txt  phone_number.txt  email_address.txt
    demo_user.txt   demo_password.txt   notes.txt
```

| File | Goes to | Notes |
|------|---------|-------|
| `copyright.txt` | the version's copyright | Write it as Apple shows it, for example `2026 Your Name`. No copyright symbol is needed |
| `first_name.txt`, `last_name.txt` | App Review contact name | Required to submit |
| `phone_number.txt` | contact phone | Include the country code, for example `+34 600 000 000` |
| `email_address.txt` | contact email | Checked for a plausible shape before sending |
| `demo_user.txt`, `demo_password.txt` | demo account | Give both or neither. Giving both also marks the demo account as required |
| `notes.txt` | notes for the reviewer | Up to 4000 characters. Say how to reach features that need hardware, a purchase or a permission |

Push them with `push_metadata.py`, alone or with the rest:

```bash
python3 scripts/push_metadata.py --app my-app --version 1.0.0 --fields copyright,review_information
```

- An existing App Review record is updated field by field; if there is none, it
  is created with everything provided.
- **The demo password is a secret in a plain text file.** The script never
  prints it and `pull_metadata.py` never writes it. It warns when the file is
  inside a git repository and not ignored, so add it to `.gitignore` before the
  first commit. Using a throwaway account for review avoids the issue.
- A phone number without a country code only warns; a demo user without a
  password, an email that is not an email, or notes over the limit stop the run
  before any request is made.

## Purchase texts

`push_purchases.py` reads the display name and description of in-app purchases
and subscriptions from the same metadata folder, one folder per product under
the kind it belongs to:

```
metadata/
  in_app_purchases/
    com.example.app.premium.lifetime/       the product ID
      en-US/
        name.txt  description.txt
      es-ES/
        ...
  subscriptions/
    com.example.app.premium.monthly/        the product ID
      en-US/
        name.txt  description.txt
  subscription_groups/
    Premium/                                the group's reference name
      en-US/
        name.txt  custom_app_name.txt
```

| File | Limit | Applies to |
|------|-------|------------|
| `name.txt` | 30 | in-app purchase, subscription (display name); subscription group (display name shown in Settings) |
| `description.txt` | 45 | in-app purchase, subscription |
| `custom_app_name.txt` | - | subscription group only; optional, replaces the app's name next to the group |

- The product folder is the product ID App Store Connect shows; the group
  folder is the group's reference name, spaces included.
- Locale folders are App Store Connect locale codes, as for the listing. Skipped
  locales (`skip_locales` in the apps file) are skipped here too.
- The same trailing-newline and empty-file rules apply, and the limits only
  reject text that is certainly too long.
- `push_metadata.py` ignores these three folders.
- A new locale needs `name.txt`. A description is advised: without one the
  product usually stays at `MISSING_METADATA`.
