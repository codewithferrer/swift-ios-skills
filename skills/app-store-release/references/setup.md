# Setup

One-time setup for credentials, the app registry and dependencies.

## 1. API key

In App Store Connect: **Users and Access -> Integrations -> App Store Connect
API -> "+"**. You need the Admin role to create it; give the key at least the
**App Manager** role so it can write metadata. Apple lets you download the
`.p8` file **once** - save it immediately.

## 2. Credentials

Credentials live outside the skill, so updating the skill never touches them:

```bash
mkdir -p ~/.config/app-store-release/keys
cp ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.config/app-store-release/keys/
cp examples/env.example ~/.config/app-store-release/.env
# edit .env: ASC_KEY_ID and ASC_ISSUER_ID are shown on the API key page
```

Set `ASC_CONFIG_DIR` to use a different directory. `ASC_KEY_ID`,
`ASC_ISSUER_ID` and `ASC_PRIVATE_KEY_PATH` can also come straight from the
environment, which wins over `.env`.

## 3. App registry

Create an `asc-apps.json` in your project (or any parent directory, so one file
can serve a whole workspace). Copy `examples/asc-apps.example.json`:

```json
{
  "my-app": {
    "app_id": "1234567890",
    "release_notes_path": "RELEASE_NOTES.md",
    "metadata_path": "metadata",
    "skip_locales": [],
    "screenshot_display_type": "APP_IPHONE_65"
  }
}
```

| Field | Required | Meaning |
|-------|----------|---------|
| `app_id` | yes | The numeric Apple ID (App Store Connect -> App Information, or the number in the App Store URL). |
| `release_notes_path` | for `push_whats_new.py` | Release notes file, relative to the apps file. |
| `metadata_path` | for `pull_metadata.py` / `push_metadata.py` | Folder holding the listing text files, relative to the apps file (or pass `--dir`). |
| `skip_locales` | no | Locale codes present in the notes file that must not be sent to App Store Connect. |
| `screenshot_display_type` | no | Default `ScreenshotDisplayType` for `upload_screenshots.py`. |

The apps file is looked up in this order: `--apps-file` / `ASC_APPS_FILE`, the
nearest `asc-apps.json` at or above the current directory, then
`<config dir>/apps.json`.

## 4. Dependencies

```bash
python3 -m venv ~/.venvs/app-store-release
~/.venvs/app-store-release/bin/pip install -r requirements.txt
```

Then run the scripts with that interpreter, or activate the venv first.
(`pip install` straight into a Homebrew or system Python is often blocked by
PEP 668.)

