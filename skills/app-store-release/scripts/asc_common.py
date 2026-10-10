"""Shared plumbing for the command-line scripts: where credentials and the
app registry live, how an --app argument is resolved, and confirmation
prompts. Deliberately free of third-party imports so `--help` and dry runs
work before requests/PyJWT are installed.

Nothing user-specific is stored inside the skill directory. Credentials live
in a config directory (default ~/.config/app-store-release, override with
ASC_CONFIG_DIR) and the list of apps lives in an `asc-apps.json` found by
walking up from the current directory. One installed copy of the skill
therefore serves any number of projects, and updating it never touches
secrets.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
APPS_FILE_NAME = "asc-apps.json"
DEFAULT_CONFIG_DIR = Path("~/.config/app-store-release").expanduser()


class ConfigError(RuntimeError):
    pass


def config_dir() -> Path:
    """Directory holding `.env`, `keys/` and (optionally) a fallback
    `apps.json`. Not created automatically -- see references/setup.md.
    """
    override = os.environ.get("ASC_CONFIG_DIR")
    return Path(override).expanduser().resolve() if override else DEFAULT_CONFIG_DIR


# -- App registry ---------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    key: str
    app_id: str
    release_notes_path: Path | None
    metadata_path: Path | None
    skip_locales: frozenset
    screenshot_display_type: str | None
    apps_file: Path


def find_apps_file(explicit: str | None = None) -> Path:
    """Lookup order: --apps-file / $ASC_APPS_FILE, then the nearest
    `asc-apps.json` in the current directory or any parent, then
    `<config dir>/apps.json`.
    """
    explicit = explicit or os.environ.get("ASC_APPS_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Apps file not found: {path}")
        return path.resolve()

    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / APPS_FILE_NAME
        if candidate.is_file():
            return candidate

    fallback = config_dir() / "apps.json"
    if fallback.is_file():
        return fallback

    raise ConfigError(
        f"No app registry found. Create {APPS_FILE_NAME} in your project (or a parent "
        f"directory), or {fallback}, or pass --apps-file. See "
        f"{SKILL_DIR / 'examples' / 'asc-apps.example.json'} for the format."
    )


def load_app(key: str | None = None, apps_file: str | None = None) -> AppConfig:
    path = find_apps_file(apps_file)
    try:
        apps = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(apps, dict) or not apps:
        raise ConfigError(f"{path} must be a JSON object with at least one app in it.")

    if key is None:
        if len(apps) != 1:
            raise ConfigError(
                f"--app is required: {path} lists several apps ({', '.join(sorted(apps))})."
            )
        key = next(iter(apps))
    if key not in apps:
        raise ConfigError(f"Unknown app '{key}'. Apps in {path}: {', '.join(sorted(apps))}")

    entry = apps[key]
    if not isinstance(entry, dict) or not entry.get("app_id"):
        raise ConfigError(f"App '{key}' in {path} needs an \"app_id\" (the numeric Apple ID).")

    def resolve(value: str | None) -> Path | None:
        if not value:
            return None
        resolved = Path(value).expanduser()
        if not resolved.is_absolute():
            # Relative to the apps file, so one registry at a workspace root
            # can point at `my-app/RELEASE_NOTES.md` regardless of the cwd.
            resolved = (path.parent / resolved).resolve()
        return resolved

    return AppConfig(
        key=key,
        app_id=str(entry["app_id"]),
        release_notes_path=resolve(entry.get("release_notes_path")),
        metadata_path=resolve(entry.get("metadata_path")),
        skip_locales=frozenset(entry.get("skip_locales", [])),
        screenshot_display_type=entry.get("screenshot_display_type"),
        apps_file=path,
    )


def load_all_apps(apps_file: str | None = None) -> list[AppConfig]:
    path = find_apps_file(apps_file)
    try:
        keys = list(json.loads(path.read_text()))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    return [load_app(key, str(path)) for key in keys]


def add_app_arguments(parser) -> None:
    parser.add_argument(
        "--app",
        help="App key from the apps file (optional when the file lists a single app)",
    )
    parser.add_argument(
        "--apps-file",
        help=f"Path to the apps file (default: nearest {APPS_FILE_NAME} at or above the "
        "current directory, else <config dir>/apps.json)",
    )


def resolve_app(args) -> AppConfig:
    return load_app(args.app, args.apps_file)


# -- Metadata folder arguments (pull_metadata.py / push_metadata.py) ---------


def add_metadata_arguments(parser) -> None:
    parser.add_argument("--dir", help="Metadata folder (default: the app's metadata_path in the apps file)")
    parser.add_argument("--locales", help="Comma-separated locales to include, e.g. en-US,es-ES (default: all)")
    parser.add_argument(
        "--fields",
        help="Comma-separated fields to include, e.g. description,keywords (default: all)",
    )


def resolve_metadata_dir(args, app: AppConfig) -> Path:
    if args.dir:
        return Path(args.dir).expanduser().resolve()
    if app.metadata_path:
        return app.metadata_path
    raise ConfigError(
        f"No metadata folder: pass --dir or set \"metadata_path\" for '{app.key}' in {app.apps_file}."
    )


def _csv_set(value: str | None) -> set | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()} or None


def selected_locales(args) -> set | None:
    return _csv_set(args.locales)


def selected_fields(args) -> set | None:
    from asc_metadata import FIELDS_BY_FILE

    fields = _csv_set(args.fields)
    unknown = sorted((fields or set()) - set(FIELDS_BY_FILE))
    if unknown:
        raise ConfigError(
            f"Unknown field(s): {', '.join(unknown)}. Valid fields: {', '.join(FIELDS_BY_FILE)}"
        )
    return fields


# -- Client import ----------------------------------------------------------


def load_client_module():
    """Imports asc_client lazily: it needs requests and PyJWT, which
    `--help` and dry runs should not require.
    """
    try:
        import asc_client
    except ImportError as exc:
        raise ConfigError(
            f"Missing dependency ({exc}). Run: pip install -r {SKILL_DIR / 'requirements.txt'}"
        ) from exc
    return asc_client


# -- Confirmations ----------------------------------------------------------


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        print("No interactive input available -- re-run with --yes to confirm.", file=sys.stderr)
        return False
    if answer in ("y", "yes"):
        return True
    print("Aborted.")
    return False


def confirm_typed(expected: str, assume_yes: bool = False) -> bool:
    """For irreversible actions: the user has to type the version string,
    which is deliberate friction -- a stray 'y' is not enough.
    """
    if assume_yes:
        return True
    try:
        typed = input(f"Type the version string ({expected}) to confirm: ").strip()
    except EOFError:
        print("No interactive input available -- re-run with --yes to confirm.", file=sys.stderr)
        return False
    if typed != expected:
        print("Version string did not match. Aborted.")
        return False
    return True


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
