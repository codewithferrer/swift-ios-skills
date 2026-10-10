"""Minimal App Store Connect API client covering exactly the handful of
release-day chores these scripts automate: editing "What's New" text,
creating a version and attaching a build, uploading screenshots, the review
submission flow, and releasing an approved version. Not a general-purpose
SDK on purpose -- the goal is avoiding the App Store Connect website for
these specific, recurring tasks, not wrapping the entire API surface.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import jwt
import requests

from asc_common import config_dir as default_config_dir

API_BASE = "https://api.appstoreconnect.apple.com/v1"
TOKEN_LIFETIME_SECONDS = 15 * 60  # Apple caps this at 20 minutes.


def _load_env_file(path: Path) -> None:
    """Tiny .env loader -- avoids adding python-dotenv as a dependency for
    three key=value lines. Does not override already-set environment vars,
    so `ASC_KEY_ID=... python push_whats_new.py ...` still works.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class AppStoreConnectError(RuntimeError):
    pass


class AppStoreConnectClient:
    def __init__(self, config_dir: Path | None = None):
        """Credentials come from ASC_KEY_ID / ASC_ISSUER_ID (and optionally
        ASC_PRIVATE_KEY_PATH) in the environment or in `<config_dir>/.env`.
        When no key path is given, Apple's default file name
        `AuthKey_<KEY_ID>.p8` is looked up in `<config_dir>/keys/`. A
        relative key path is resolved against the config dir.
        """
        config_dir = config_dir or default_config_dir()
        _load_env_file(config_dir / ".env")

        key_id = os.environ.get("ASC_KEY_ID")
        issuer_id = os.environ.get("ASC_ISSUER_ID")
        if not (key_id and issuer_id):
            raise AppStoreConnectError(
                f"Missing ASC_KEY_ID / ASC_ISSUER_ID. Set them in the environment or in "
                f"{config_dir / '.env'} (see references/setup.md)."
            )

        key_path = os.environ.get("ASC_PRIVATE_KEY_PATH") or f"keys/AuthKey_{key_id}.p8"
        private_key_file = Path(key_path).expanduser()
        if not private_key_file.is_absolute():
            private_key_file = config_dir / private_key_file
        private_key_file = private_key_file.resolve()
        if not private_key_file.exists():
            raise AppStoreConnectError(f"Private key not found at {private_key_file}")

        self._key_id = key_id
        self._issuer_id = issuer_id
        self._private_key = private_key_file.read_text()
        self._token: str | None = None
        self._token_expires_at = 0.0

    # -- Auth -----------------------------------------------------------

    def _bearer_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        now = int(time.time())
        payload = {
            "iss": self._issuer_id,
            "iat": now,
            "exp": now + TOKEN_LIFETIME_SECONDS,
            "aud": "appstoreconnect-v1",
        }
        self._token = jwt.encode(
            payload,
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_id},
        )
        self._token_expires_at = now + TOKEN_LIFETIME_SECONDS
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._bearer_token()}",
            "Content-Type": "application/json",
        }

    # -- Low-level HTTP ---------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        response = requests.request(method, url, headers=self._headers(), timeout=30, **kwargs)
        if response.status_code >= 400:
            raise AppStoreConnectError(
                f"{method} {url} -> {response.status_code}: {response.text}"
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    # -- App Store Connect resources --------------------------------------

    def find_version(self, app_id: str, version_string: str) -> dict:
        """Returns the appStoreVersions resource for an exact version string
        (the editable one -- there's normally only one non-released version
        at a time, but filtering by versionString avoids ever guessing).
        """
        version = self.find_version_or_none(app_id, version_string)
        if version is None:
            raise AppStoreConnectError(
                f"No appStoreVersions found for app {app_id} with versionString "
                f"'{version_string}' -- create the version in App Store Connect first "
                "(Distribution -> + Version), or run create_version.py."
            )
        return version

    def find_version_or_none(self, app_id: str, version_string: str) -> dict | None:
        data = self._request(
            "GET",
            f"/apps/{app_id}/appStoreVersions",
            params={"filter[versionString]": version_string, "limit": 1},
        )
        versions = data.get("data", [])
        return versions[0] if versions else None

    def create_version(
        self,
        app_id: str,
        version_string: str,
        platform: str = "IOS",
        release_type: str = "MANUAL",
    ) -> dict:
        """Creates a new appStoreVersions record -- the API equivalent of
        Distribution -> "+ Version" in App Store Connect. Does not touch the
        binary: a build still has to be uploaded separately (Xcode/Transporter)
        and then attached with assign_build().
        """
        data = self._request(
            "POST",
            "/appStoreVersions",
            json={
                "data": {
                    "type": "appStoreVersions",
                    "attributes": {
                        "platform": platform,
                        "versionString": version_string,
                        "releaseType": release_type,
                    },
                    "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
                }
            },
        )
        return data["data"]

    def list_builds(self, app_id: str, build_number: str | None = None, only_valid: bool = True) -> list[dict]:
        """Builds already uploaded and processed by Apple, newest first.
        `build_number` filters to an exact CFBundleVersion (the build's
        `version` attribute, distinct from the app's marketing versionString).
        """
        params = {"filter[app]": app_id, "sort": "-uploadedDate", "limit": 10}
        if build_number:
            params["filter[version]"] = build_number
        if only_valid:
            params["filter[processingState]"] = "VALID"
        data = self._request("GET", "/builds", params=params)
        return data.get("data", [])

    def assign_build(self, version_id: str, build_id: str) -> None:
        self._request(
            "PATCH",
            f"/appStoreVersions/{version_id}/relationships/build",
            json={"data": {"type": "builds", "id": build_id}},
        )

    def list_localizations(self, version_id: str) -> dict:
        """locale -> localization resource, for an appStoreVersions id."""
        data = self._request(
            "GET",
            f"/appStoreVersions/{version_id}/appStoreVersionLocalizations",
            params={"limit": 50},
        )
        return {item["attributes"]["locale"]: item for item in data.get("data", [])}

    def update_localization(self, localization_id: str, attributes: dict) -> None:
        self._request(
            "PATCH",
            f"/appStoreVersionLocalizations/{localization_id}",
            json={
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "id": localization_id,
                    "attributes": attributes,
                }
            },
        )

    # -- App info (name, subtitle, privacy policy URL) ---------------------
    #
    # Distinct from appStoreVersionLocalizations above: these fields live on
    # the app-level appInfos resource, not the per-version one.
    #
    # `/apps/{id}/appInfos` returns one entry per app info state. The live
    # one (READY_FOR_SALE) is locked; a second, editable one exists only
    # while a version is being prepared. When the app has no editable entry,
    # `find_editable_app_info` returns None rather than falling back to the
    # locked one, which would only fail later with a confusing 409.
    #
    # The API reports the state twice, as `appStoreState` and as the newer
    # `state`, with different values for the same moment (READY_FOR_SALE vs
    # READY_FOR_DISTRIBUTION). Either one marks the entry as live.

    LIVE_APP_INFO_STATES = frozenset({"READY_FOR_SALE", "READY_FOR_DISTRIBUTION"})

    def list_app_infos(self, app_id: str) -> list[dict]:
        return self._request("GET", f"/apps/{app_id}/appInfos").get("data", [])

    def find_editable_app_info(self, app_id: str) -> dict | None:
        for info in self.list_app_infos(app_id):
            attrs = info["attributes"]
            states = {attrs.get("appStoreState"), attrs.get("state")}
            if not (states & self.LIVE_APP_INFO_STATES):
                return info
        return None

    def list_app_info_localizations(self, app_info_id: str) -> dict:
        """locale -> localization resource, for one appInfos id."""
        data = self._request(
            "GET",
            f"/appInfos/{app_info_id}/appInfoLocalizations",
            params={"limit": 50},
        )
        return {item["attributes"]["locale"]: item for item in data.get("data", [])}

    def update_app_info_localization(self, localization_id: str, attributes: dict) -> None:
        self._request(
            "PATCH",
            f"/appInfoLocalizations/{localization_id}",
            json={
                "data": {
                    "type": "appInfoLocalizations",
                    "id": localization_id,
                    "attributes": attributes,
                }
            },
        )

    def create_app_info_localization(self, app_info_id: str, locale: str, attributes: dict) -> None:
        """A new locale needs `name` among the attributes."""
        body = dict(attributes)
        body["locale"] = locale
        self._request(
            "POST",
            "/appInfoLocalizations",
            json={
                "data": {
                    "type": "appInfoLocalizations",
                    "attributes": body,
                    "relationships": {"appInfo": {"data": {"type": "appInfos", "id": app_info_id}}},
                }
            },
        )

    def create_localization(self, version_id: str, locale: str, attributes: dict) -> None:
        body = dict(attributes)
        body["locale"] = locale
        self._request(
            "POST",
            "/appStoreVersionLocalizations",
            json={
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "attributes": body,
                    "relationships": {
                        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
                    },
                }
            },
        )

    # -- Review submission (3-step flow that replaced the old, retired
    #    appStoreVersionSubmissions resource) ------------------------------
    #
    # Step 1 (create_review_submission) and step 2 (add_version_to_review_
    # submission) only build a draft container -- nothing reaches Apple's
    # review queue until step 3 (submit_review_submission) explicitly sets
    # submitted=true. Keep these as three separate calls, never collapse
    # them, so a caller can stop after step 2 and inspect the draft in App
    # Store Connect before pulling the trigger on step 3.

    def create_review_submission(self, app_id: str) -> dict:
        data = self._request(
            "POST",
            "/reviewSubmissions",
            json={
                "data": {
                    "type": "reviewSubmissions",
                    "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
                }
            },
        )
        return data["data"]

    def add_version_to_review_submission(self, submission_id: str, version_id: str) -> dict:
        data = self._request(
            "POST",
            "/reviewSubmissionItems",
            json={
                "data": {
                    "type": "reviewSubmissionItems",
                    "relationships": {
                        "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": submission_id}},
                        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
                    },
                }
            },
        )
        return data["data"]

    def submit_review_submission(self, submission_id: str) -> None:
        """The actual point of no return -- everything before this call is
        just preparation, reversible by simply not calling this.
        """
        self._request(
            "PATCH",
            f"/reviewSubmissions/{submission_id}",
            json={
                "data": {
                    "type": "reviewSubmissions",
                    "id": submission_id,
                    "attributes": {"submitted": True},
                }
            },
        )

    # -- Release (manual release after approval) ---------------------------
    #
    # Only relevant when the version's releaseType is MANUAL: after Apple
    # approves it, appStoreState sits at PENDING_DEVELOPER_RELEASE until this
    # is called. Confirmed against Apple's own OpenAPI spec -- a bare POST,
    # no attributes, just a relationship to the version. There is no GET,
    # PATCH, or DELETE for this resource: it is a one-shot trigger with no
    # API-level undo once it succeeds.

    def release_version(self, version_id: str) -> None:
        self._request(
            "POST",
            "/appStoreVersionReleaseRequests",
            json={
                "data": {
                    "type": "appStoreVersionReleaseRequests",
                    "relationships": {
                        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
                    },
                }
            },
        )

    # -- Screenshots ------------------------------------------------------
    #
    # Field names and the upload choreography below are taken directly from
    # Apple's published OpenAPI spec (ScreenshotDisplayType, AppScreenshotSet/
    # AppScreenshot schemas), not guessed: reserve -> PUT the bytes to the
    # pre-signed uploadOperations URLs (unauthenticated -- no bearer token on
    # those requests) -> PATCH uploaded=true with the MD5 checksum -> poll
    # assetDeliveryState until it says COMPLETE.

    def list_screenshot_sets(self, localization_id: str) -> list[dict]:
        data = self._request(
            "GET", f"/appStoreVersionLocalizations/{localization_id}/appScreenshotSets"
        )
        return data.get("data", [])

    def create_screenshot_set(self, localization_id: str, display_type: str) -> dict:
        data = self._request(
            "POST",
            "/appScreenshotSets",
            json={
                "data": {
                    "type": "appScreenshotSets",
                    "attributes": {"screenshotDisplayType": display_type},
                    "relationships": {
                        "appStoreVersionLocalization": {
                            "data": {"type": "appStoreVersionLocalizations", "id": localization_id}
                        }
                    },
                }
            },
        )
        return data["data"]

    def list_screenshots(self, set_id: str) -> list[dict]:
        data = self._request("GET", f"/appScreenshotSets/{set_id}/appScreenshots")
        return data.get("data", [])

    def delete_screenshot(self, screenshot_id: str) -> None:
        self._request("DELETE", f"/appScreenshots/{screenshot_id}")

    def reserve_screenshot_upload(self, set_id: str, file_name: str, file_size: int) -> dict:
        data = self._request(
            "POST",
            "/appScreenshots",
            json={
                "data": {
                    "type": "appScreenshots",
                    "attributes": {"fileName": file_name, "fileSize": file_size},
                    "relationships": {
                        "appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}
                    },
                }
            },
        )
        return data["data"]

    def upload_screenshot_bytes(self, upload_operations: list[dict], file_bytes: bytes) -> None:
        for op in upload_operations:
            start, end = op["offset"], op["offset"] + op["length"]
            headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
            # Deliberately not using self._headers() here -- these pre-signed
            # URLs reject an Authorization header, per Apple's own docs.
            response = requests.request(
                op.get("method", "PUT"),
                op["url"],
                headers=headers,
                data=file_bytes[start:end],
                timeout=60,
            )
            if response.status_code >= 400:
                raise AppStoreConnectError(
                    f"Upload PUT to {op['url']} -> {response.status_code}: {response.text}"
                )

    def commit_screenshot_upload(self, screenshot_id: str, checksum: str) -> None:
        self._request(
            "PATCH",
            f"/appScreenshots/{screenshot_id}",
            json={
                "data": {
                    "type": "appScreenshots",
                    "id": screenshot_id,
                    "attributes": {"uploaded": True, "sourceFileChecksum": checksum},
                }
            },
        )

    def get_screenshot(self, screenshot_id: str) -> dict:
        data = self._request("GET", f"/appScreenshots/{screenshot_id}")
        return data["data"]

    def order_screenshots(self, set_id: str, screenshot_ids: list[str]) -> None:
        """Sets the display order within a set -- a plain JSON:API to-many
        relationship replace, where array order becomes display order.
        """
        self._request(
            "PATCH",
            f"/appScreenshotSets/{set_id}/relationships/appScreenshots",
            json={"data": [{"type": "appScreenshots", "id": sid} for sid in screenshot_ids]},
        )
