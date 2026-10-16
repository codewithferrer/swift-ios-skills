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

API_ROOT = "https://api.appstoreconnect.apple.com"
API_BASE = f"{API_ROOT}/v1"
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
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
        # Reads are retried once on a dropped connection or a timeout: they are
        # safe to repeat and Apple's edge occasionally closes a connection.
        # Writes are never retried, so a request that may have gone through is
        # not sent twice.
        attempts = 2 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                response = requests.request(method, url, headers=self._headers(), timeout=30, **kwargs)
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                if attempt + 1 < attempts:
                    time.sleep(1)
                    continue
                raise AppStoreConnectError(
                    f"{method} {url} failed: {type(exc).__name__}. Check the connection and try again."
                    + ("" if method.upper() == "GET" else " The request may or may not have been applied.")
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise AppStoreConnectError(f"{method} {url} failed: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise AppStoreConnectError(
                f"{method} {url} -> {response.status_code}: {response.text}",
                status_code=response.status_code,
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

    # -- Status and diagnostics (read-only) --------------------------------

    def list_versions(self, app_id: str, limit: int = 5) -> list[dict]:
        """appStoreVersions, newest first."""
        data = self._request("GET", f"/apps/{app_id}/appStoreVersions", params={"limit": limit})
        return data.get("data", [])

    def list_build_uploads(self, app_id: str, limit: int = 10, build_number: str | None = None) -> list[dict]:
        """Uploads Apple has received, newest first, whatever their state.

        Unlike list_builds, which only knows builds that finished processing,
        this still shows an upload that failed, with the errors Apple reported
        in `attributes.state.errors`.
        """
        params = {"limit": limit, "sort": "-uploadedDate"}
        if build_number:
            params["filter[cfBundleVersion]"] = build_number
        return self._request("GET", f"/apps/{app_id}/buildUploads", params=params).get("data", [])

    def list_ci_build_runs(self, app_id: str, limit: int = 3) -> list[dict] | None:
        """Newest Xcode Cloud runs, or None when the app has no Xcode Cloud product."""
        try:
            product = self._request("GET", f"/apps/{app_id}/ciProduct")
        except AppStoreConnectError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not product.get("data"):
            return None
        runs = self._request(
            "GET",
            f"/ciProducts/{product['data']['id']}/buildRuns",
            params={"limit": limit, "sort": "-number"},
        )
        return runs.get("data", [])

    # -- Customer reviews ---------------------------------------------------
    #
    # A response is a public reply under the review. Apple creates it as
    # PENDING_PUBLISH and publishes it shortly after. The API has no way to
    # edit one: delete it and post a new one.

    def list_customer_reviews(
        self,
        app_id: str,
        limit: int = 50,
        rating: int | None = None,
        territory: str | None = None,
        unanswered: bool = False,
    ) -> tuple[list[dict], dict]:
        """(reviews newest first, {response id: response resource}). Only
        written reviews are returned; star-only ratings are not exposed.
        """
        params = {"limit": limit, "sort": "-createdDate", "include": "response"}
        if rating is not None:
            params["filter[rating]"] = rating
        if territory:
            params["filter[territory]"] = territory
        if unanswered:
            params["exists[publishedResponse]"] = "false"
        data = self._request("GET", f"/apps/{app_id}/customerReviews", params=params)
        responses = {
            item["id"]: item for item in data.get("included", []) if item.get("type") == "customerReviewResponses"
        }
        return data.get("data", []), responses

    def list_review_summarizations(self, app_id: str, platform: str = "IOS") -> list[dict]:
        data = self._request(
            "GET",
            f"/apps/{app_id}/customerReviewSummarizations",
            params={"filter[platform]": platform},
        )
        return data.get("data", [])

    def get_customer_review(self, review_id: str) -> dict:
        return self._request("GET", f"/customerReviews/{review_id}")["data"]

    def get_review_response(self, review_id: str) -> dict | None:
        """The public reply under a review, or None when it has none."""
        try:
            data = self._request("GET", f"/customerReviews/{review_id}/response")
        except AppStoreConnectError as exc:
            if exc.status_code == 404:
                return None
            raise
        return data.get("data")

    def create_review_response(self, review_id: str, body: str) -> dict:
        data = self._request(
            "POST",
            "/customerReviewResponses",
            json={
                "data": {
                    "type": "customerReviewResponses",
                    "attributes": {"responseBody": body},
                    "relationships": {"review": {"data": {"type": "customerReviews", "id": review_id}}},
                }
            },
        )
        return data["data"]

    def delete_review_response(self, response_id: str) -> None:
        self._request("DELETE", f"/customerReviewResponses/{response_id}")

    # -- Localization coverage (read-only) -----------------------------------
    #
    # In-app purchases use the v2 resource family, so their localizations
    # live under /v2. Game Center leaderboards and achievements are v1.

    def list_in_app_purchases(self, app_id: str) -> list[dict]:
        return self._request("GET", f"/apps/{app_id}/inAppPurchasesV2", params={"limit": 200}).get("data", [])

    def list_iap_localizations(self, iap_id: str) -> dict:
        """locale -> localization resource, for an in-app purchase (v2) id."""
        data = self._request(
            "GET",
            f"{API_ROOT}/v2/inAppPurchases/{iap_id}/inAppPurchaseLocalizations",
            params={"limit": 200},
        )
        return {item["attributes"]["locale"]: item for item in data.get("data", [])}

    def find_game_center_detail_id(self, app_id: str) -> str | None:
        try:
            data = self._request("GET", f"/apps/{app_id}/gameCenterDetail")
        except AppStoreConnectError as exc:
            if exc.status_code == 404:
                return None
            raise
        return data["data"]["id"] if data.get("data") else None

    def list_leaderboards(self, game_center_detail_id: str) -> list[dict]:
        data = self._request(
            "GET", f"/gameCenterDetails/{game_center_detail_id}/gameCenterLeaderboards", params={"limit": 200}
        )
        return data.get("data", [])

    def list_achievements(self, game_center_detail_id: str) -> list[dict]:
        data = self._request(
            "GET", f"/gameCenterDetails/{game_center_detail_id}/gameCenterAchievements", params={"limit": 200}
        )
        return data.get("data", [])

    def list_game_center_localizations(self, kind: str, resource_id: str) -> dict:
        """locale -> localization resource for a leaderboard or achievement.
        `kind` is "gameCenterLeaderboards" or "gameCenterAchievements".
        """
        data = self._request("GET", f"/{kind}/{resource_id}/localizations", params={"limit": 200})
        return {item["attributes"]["locale"]: item for item in data.get("data", [])}

    # -- Pre-submission reads --------------------------------------------------

    def _optional(self, path: str, params: dict | None = None):
        """`data` of a GET, or None when the resource does not exist (a 404
        or an explicit null), as opposed to an error worth surfacing.
        """
        try:
            return self._request("GET", path, params=params or {}).get("data")
        except AppStoreConnectError as exc:
            if exc.status_code == 404:
                return None
            raise

    def get_version_build(self, version_id: str) -> dict | None:
        """The build attached to a version, or None when none is."""
        return self._optional(f"/appStoreVersions/{version_id}/build")

    def get_review_detail(self, version_id: str) -> dict | None:
        """App Review information (contact, demo account, notes), or None
        when it has not been created yet.
        """
        return self._optional(f"/appStoreVersions/{version_id}/appStoreReviewDetail")

    def get_app(self, app_id: str) -> dict:
        return self._request("GET", f"/apps/{app_id}")["data"]

    def app_info_has(self, app_info_id: str, relationship: str) -> bool:
        """Whether an appInfo has a to-one relationship set, such as
        "primaryCategory" or "ageRatingDeclaration".
        """
        return self._optional(f"/appInfos/{app_info_id}/{relationship}") is not None

    def get_price_schedule(self, app_id: str) -> dict | None:
        return self._optional(f"/apps/{app_id}/appPriceSchedule")

    def list_accessibility_declarations(self, app_id: str) -> list[dict]:
        return self._request("GET", f"/apps/{app_id}/accessibilityDeclarations", params={"limit": 20}).get("data", [])

    def list_subscription_groups(self, app_id: str) -> list[dict]:
        return self._request("GET", f"/apps/{app_id}/subscriptionGroups", params={"limit": 50}).get("data", [])

    def list_subscriptions(self, app_id: str) -> list[dict]:
        """Every subscription of the app, across its subscription groups."""
        groups = self.list_subscription_groups(app_id)
        found = []
        for group in groups:
            data = self._request("GET", f"/subscriptionGroups/{group['id']}/subscriptions", params={"limit": 50})
            found.extend(data.get("data", []))
        return found

    # -- Purchase localizations (display name and description) -------------------
    #
    # Field and relationship names are taken from Apple's published OpenAPI
    # spec (4.5.1): the v1 create/update requests, not the v2 ones, which
    # attach a localization to a content *version* of a purchase.
    #
    #   kind                  resource type                    relationship          parent type
    PURCHASE_LOCALIZATIONS = {
        "in_app_purchase": ("inAppPurchaseLocalizations", "inAppPurchaseV2", "inAppPurchases"),
        "subscription": ("subscriptionLocalizations", "subscription", "subscriptions"),
        "subscription_group": ("subscriptionGroupLocalizations", "subscriptionGroup", "subscriptionGroups"),
    }

    def list_purchase_localizations(self, kind: str, parent_id: str) -> dict:
        """locale -> localization resource, for an in-app purchase (v2) id, a
        subscription id or a subscription group id, depending on `kind`.
        """
        if kind == "in_app_purchase":
            return self.list_iap_localizations(parent_id)
        path = {
            "subscription": f"/subscriptions/{parent_id}/subscriptionLocalizations",
            "subscription_group": f"/subscriptionGroups/{parent_id}/subscriptionGroupLocalizations",
        }[kind]
        data = self._request("GET", path, params={"limit": 200})
        return {item["attributes"]["locale"]: item for item in data.get("data", [])}

    def create_purchase_localization(self, kind: str, parent_id: str, locale: str, attributes: dict) -> None:
        """A new locale needs `name` among the attributes."""
        resource, relationship, parent_type = self.PURCHASE_LOCALIZATIONS[kind]
        body = dict(attributes)
        body["locale"] = locale
        self._request(
            "POST",
            f"/{resource}",
            json={
                "data": {
                    "type": resource,
                    "attributes": body,
                    "relationships": {relationship: {"data": {"type": parent_type, "id": parent_id}}},
                }
            },
        )

    def update_purchase_localization(self, kind: str, localization_id: str, attributes: dict) -> None:
        resource = self.PURCHASE_LOCALIZATIONS[kind][0]
        self._request(
            "PATCH",
            f"/{resource}/{localization_id}",
            json={"data": {"type": resource, "id": localization_id, "attributes": attributes}},
        )

    # -- Version-level writes -----------------------------------------------------

    def update_version(self, version_id: str, attributes: dict) -> None:
        """PATCH attributes of an appStoreVersions record, such as `copyright`."""
        self._request(
            "PATCH",
            f"/appStoreVersions/{version_id}",
            json={"data": {"type": "appStoreVersions", "id": version_id, "attributes": attributes}},
        )

    def create_review_detail(self, version_id: str, attributes: dict) -> dict:
        data = self._request(
            "POST",
            "/appStoreReviewDetails",
            json={
                "data": {
                    "type": "appStoreReviewDetails",
                    "attributes": attributes,
                    "relationships": {
                        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
                    },
                }
            },
        )
        return data["data"]

    def update_review_detail(self, detail_id: str, attributes: dict) -> None:
        self._request(
            "PATCH",
            f"/appStoreReviewDetails/{detail_id}",
            json={"data": {"type": "appStoreReviewDetails", "id": detail_id, "attributes": attributes}},
        )
