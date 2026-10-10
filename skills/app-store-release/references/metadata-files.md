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

