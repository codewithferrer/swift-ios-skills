"""Offline tests for the status report, localization coverage and reviews.

    python3 -m unittest discover -s tests -v

No network or credentials. The client tests are skipped when requests and
PyJWT are not installed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import asc_status as st  # noqa: E402

try:
    import asc_client  # noqa: E402

    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False


def upload(build, state, uploaded="2026-09-10T10:00:00+00:00", errors=None, warnings=None, short="1.0"):
    return {
        "id": f"u{build}",
        "attributes": {
            "cfBundleVersion": str(build),
            "cfBundleShortVersionString": short,
            "createdDate": uploaded or "2026-09-10T09:00:00+00:00",
            "uploadedDate": uploaded,
            "state": {"state": state, "errors": errors or [], "warnings": warnings or [], "infos": []},
        },
    }


class UploadTests(unittest.TestCase):
    def test_only_unfinished_uploads_newer_than_the_latest_processed_build_count(self):
        since = st.parse_date("2026-09-16T00:00:00+00:00")
        uploads = [
            upload(27, "FAILED", "2026-09-20T00:00:00+00:00"),       # newer and failed -> problem
            upload(26, "PROCESSING", "2026-09-21T00:00:00+00:00"),   # newer and stuck -> problem
            upload(25, "COMPLETE", "2026-09-17T00:00:00+00:00"),     # fine
            upload(19, "FAILED", "2026-06-28T00:00:00+00:00"),       # old failure -> history
            upload(2, "AWAITING_UPLOAD", None),                      # created long ago, never uploaded
        ]
        uploads[-1]["attributes"]["createdDate"] = "2026-05-01T00:00:00+00:00"
        problems = st.unfinished_uploads(uploads, since)
        self.assertEqual([u["attributes"]["cfBundleVersion"] for u in problems], ["27", "26"])

    def test_without_a_reference_date_every_unfinished_upload_counts(self):
        problems = st.unfinished_uploads([upload(1, "FAILED"), upload(2, "COMPLETE")], None)
        self.assertEqual(len(problems), 1)

    def test_describe_upload_includes_error_codes_and_warning_count(self):
        lines = st.describe_upload(upload(
            19, "FAILED",
            errors=[{"code": "90022", "description": "Missing required icon file."}],
            warnings=[{"code": "W1", "description": "Something minor."}],
        ))
        self.assertIn("build 19", lines[0])
        self.assertIn("FAILED", lines[0])
        self.assertTrue(any("error 90022: Missing required icon file." in l for l in lines))
        self.assertTrue(any("1 warning(s)" in l for l in lines))

    def test_explain_missing_build(self):
        runs = [{"attributes": {"number": 26, "completionStatus": "SUCCEEDED", "createdDate": "2026-09-17", "startReason": "GIT_REF_CHANGE"}}]
        none_found = st.explain_missing_build([], runs, "999")
        self.assertTrue(any("no upload for build 999" in l for l in none_found))
        self.assertTrue(any("latest run: run #26" in l for l in none_found))

        matched = st.explain_missing_build([upload(26, "FAILED")], runs, "26")
        self.assertTrue(any(l.startswith("Xcode Cloud: run #26") for l in matched))


class StatusRenderTests(unittest.TestCase):
    def version(self, string, state):
        return {"attributes": {"versionString": string, "appVersionState": state}}

    def test_only_the_newest_live_version_is_called_live(self):
        text = st.render_status("app", "1", {
            "versions": [
                self.version("2.0", "PREPARE_FOR_SUBMISSION"),
                self.version("1.9", "READY_FOR_DISTRIBUTION"),
                self.version("1.8", "READY_FOR_DISTRIBUTION"),
            ],
        })
        self.assertIn("editable draft", text)
        self.assertEqual(text.count("live"), 1)
        self.assertIn("earlier release", text)

    def test_sections_and_unavailable_notes(self):
        text = st.render_status("app", "1", {
            "build": {"attributes": {"version": "22", "processingState": "VALID", "uploadedDate": "2026-09-16T00:00:00Z"}},
            "uploads": [], "problem_uploads": [],
            "ci_runs": None,
            "reviews": {"unanswered": 3},
            "notes": ["builds: HTTP 403"],
        })
        self.assertIn("nothing stuck or failed", text)
        self.assertIn("Xcode Cloud      not set up", text)
        self.assertIn("3 written review(s) awaiting a reply", text)
        self.assertIn("(not available: builds: HTTP 403)", text)

    def test_problem_uploads_are_shown_prominently(self):
        text = st.render_status("app", "1", {
            "uploads": [upload(5, "FAILED")], "problem_uploads": [upload(5, "FAILED", errors=[{"code": "1", "description": "bad"}])],
        })
        self.assertIn("NEEDS ATTENTION", text)
        self.assertIn("error 1: bad", text)


class CoverageTests(unittest.TestCase):
    def test_regional_stand_in_rules(self):
        self.assertEqual(st.regional_stand_in("en-US", {"en-GB", "de-DE"}), "en-GB")
        self.assertEqual(st.regional_stand_in("es-MX", {"es-ES"}), "es-ES")
        self.assertIsNone(st.regional_stand_in("ja", {"ko"}))
        # Simplified and Traditional Chinese are different scripts, not regions.
        self.assertIsNone(st.regional_stand_in("zh-Hant", {"zh-Hans"}))

    def test_absent_versus_region_only(self):
        row = st.Coverage("Leaderboard", "Easy", {"en-GB", "de-DE"}, {"en-US", "de-DE", "ja", "zh-Hans"})
        self.assertEqual(row.missing, ["en-US", "ja", "zh-Hans"])
        self.assertEqual(row.region_only, {"en-US": "en-GB"})
        self.assertEqual(row.absent, ["ja", "zh-Hans"])
        self.assertFalse(row.ok)

    def test_render_distinguishes_gap_note_and_ok(self):
        expected = {"en-US", "es-MX", "ja"}
        rows = [
            st.Coverage("Listing", "description", {"en-US", "es-MX", "ja"}, expected),
            st.Coverage("Leaderboard", "Hard", {"en-US"}, expected),
            st.Coverage("Leaderboard", "Easy", {"en-US", "es-ES", "ja"}, expected),
        ]
        text = st.render_coverage("app", "(test)", expected, rows)
        self.assertIn("ok   Listing: description", text)
        self.assertIn("GAP  Leaderboard: Hard", text)
        self.assertIn("missing: es-MX, ja", text)
        self.assertIn("~    Leaderboard: Easy", text)
        self.assertIn("region only: es-MX (has es-ES)", text)
        self.assertIn("1 gap(s), 1 region-only note(s), in 3 check(s)", text)

    def test_listing_and_app_info_fields_ignore_blank_values(self):
        locs = {
            "en-US": {"attributes": {"description": "d", "keywords": "k", "whatsNew": "w", "name": "N", "subtitle": "S"}},
            "es-ES": {"attributes": {"description": "d", "keywords": "  ", "whatsNew": None, "name": "N", "subtitle": ""}},
        }
        expected = {"en-US", "es-ES"}
        listing = {r.name: r for r in st.listing_field_coverage(locs, expected)}
        self.assertEqual(listing["description"].missing, [])
        self.assertEqual(listing["keywords"].missing, ["es-ES"])
        self.assertEqual(listing["What's New"].missing, ["es-ES"])
        info = {r.name: r for r in st.app_info_field_coverage(locs, expected)}
        self.assertEqual(info["subtitle"].missing, ["es-ES"])


class ReviewRenderTests(unittest.TestCase):
    review = {
        "id": "rev-1",
        "attributes": {"rating": 2, "title": "Too hard", "body": "x" * 600,
                       "reviewerNickname": "player1", "createdDate": "2026-09-20T10:00:00Z", "territory": "ESP"},
        "relationships": {"response": {"data": {"id": "resp-1", "type": "customerReviewResponses"}}},
    }

    def test_unanswered(self):
        text = st.render_review({**self.review, "relationships": {}}, {})
        self.assertIn("[2/5] 2026-09-20  ESP", text)
        self.assertIn("review id: rev-1", text)
        self.assertIn("reply: none", text)
        self.assertIn("[...]", text)  # 600 chars truncated at the default 500

    def test_answered_shows_state_and_body(self):
        responses = {"resp-1": {"id": "resp-1", "attributes": {"state": "PUBLISHED", "responseBody": "Thanks!\nWe will look."}}}
        text = st.render_review(self.review, responses)
        self.assertIn("reply (PUBLISHED, response id resp-1)", text)
        self.assertIn("Thanks!", text)
        self.assertIn("We will look.", text)


@unittest.skipUnless(HAVE_CLIENT, "requests / PyJWT not installed")
class ClientTests(unittest.TestCase):
    def make_client(self, responses=None):
        client = object.__new__(asc_client.AppStoreConnectClient)
        client.calls = []

        def fake_request(method, path, **kwargs):
            client.calls.append((method, path, kwargs))
            result = (responses or {}).get((method, path), {})
            if isinstance(result, Exception):
                raise result
            return result

        client._request = fake_request
        return client

    def test_error_carries_status_code(self):
        self.assertEqual(asc_client.AppStoreConnectError("x", status_code=404).status_code, 404)
        self.assertIsNone(asc_client.AppStoreConnectError("x").status_code)

    def test_create_review_response_payload(self):
        client = self.make_client({("POST", "/customerReviewResponses"): {"data": {"id": "r1"}}})
        out = client.create_review_response("review-9", "Thank you!")
        self.assertEqual(out, {"id": "r1"})
        method, path, kwargs = client.calls[0]
        body = kwargs["json"]["data"]
        self.assertEqual(body["type"], "customerReviewResponses")
        self.assertEqual(body["attributes"], {"responseBody": "Thank you!"})
        self.assertEqual(body["relationships"]["review"]["data"], {"type": "customerReviews", "id": "review-9"})

    def test_list_reviews_filters_and_splits_included_responses(self):
        payload = {
            "data": [{"id": "a"}],
            "included": [{"type": "customerReviewResponses", "id": "resp"}, {"type": "other", "id": "x"}],
        }
        client = self.make_client({("GET", "/apps/42/customerReviews"): payload})
        reviews, responses = client.list_customer_reviews("42", limit=10, rating=1, territory="USA", unanswered=True)
        self.assertEqual([r["id"] for r in reviews], ["a"])
        self.assertEqual(list(responses), ["resp"])
        params = client.calls[0][2]["params"]
        self.assertEqual(params["filter[rating]"], 1)
        self.assertEqual(params["filter[territory]"], "USA")
        self.assertEqual(params["exists[publishedResponse]"], "false")
        self.assertEqual(params["sort"], "-createdDate")
        self.assertEqual(params["include"], "response")

    def test_get_review_response_returns_none_on_404(self):
        err = asc_client.AppStoreConnectError("nope", status_code=404)
        client = self.make_client({("GET", "/customerReviews/r/response"): err})
        self.assertIsNone(client.get_review_response("r"))
        boom = asc_client.AppStoreConnectError("forbidden", status_code=403)
        client = self.make_client({("GET", "/customerReviews/r/response"): boom})
        with self.assertRaises(asc_client.AppStoreConnectError):
            client.get_review_response("r")

    def test_ci_runs_none_without_product_and_sorted_newest_first_with_one(self):
        err = asc_client.AppStoreConnectError("nope", status_code=404)
        self.assertIsNone(self.make_client({("GET", "/apps/1/ciProduct"): err}).list_ci_build_runs("1"))
        client = self.make_client({
            ("GET", "/apps/1/ciProduct"): {"data": {"id": "p1"}},
            ("GET", "/ciProducts/p1/buildRuns"): {"data": [{"id": "run"}]},
        })
        self.assertEqual(client.list_ci_build_runs("1", limit=2), [{"id": "run"}])
        self.assertEqual(client.calls[1][2]["params"], {"limit": 2, "sort": "-number"})

    def test_build_uploads_query(self):
        client = self.make_client({("GET", "/apps/1/buildUploads"): {"data": []}})
        client.list_build_uploads("1", limit=5, build_number="19")
        params = client.calls[0][2]["params"]
        self.assertEqual(params["filter[cfBundleVersion]"], "19")
        self.assertEqual(params["sort"], "-uploadedDate")

    def test_iap_localizations_use_the_v2_path(self):
        url = "https://api.appstoreconnect.apple.com/v2/inAppPurchases/iap1/inAppPurchaseLocalizations"
        client = self.make_client({("GET", url): {"data": [{"attributes": {"locale": "en-US"}}]}})
        self.assertEqual(list(client.list_iap_localizations("iap1")), ["en-US"])


if __name__ == "__main__":
    unittest.main()
