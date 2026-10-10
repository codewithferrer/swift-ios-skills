"""Offline tests for the pre-submission readiness rules.

    python3 -m unittest discover -s tests -v

The rules are the point of the feature: a wrong "ready" is worse than no
report, so each one is pinned here, including the "could not be read" case,
which must never count as a pass.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import asc_readiness as rd  # noqa: E402

READY = {
    "version_string": "1.0.0",
    "first_release": True,
    "copyright": "2026 Daniel Ferrer",
    "primary_locale": "en-US",
    "build": {"version": "3", "processingState": "VALID", "usesNonExemptEncryption": False},
    "latest_build": {"version": "3", "processingState": "VALID"},
    "locales": {
        "en-US": {"description": "d", "keywords": "k", "supportUrl": "https://x", "whatsNew": None},
        "es-ES": {"description": "d", "keywords": "k", "supportUrl": "https://x", "whatsNew": None},
    },
    "screenshots": {"en-US": {"APP_IPHONE_67": 5}, "es-ES": {}},
    "app_info": {
        "available": True,
        "privacy_urls": {"en-US": "https://p", "es-ES": "https://p"},
        "subtitles": {"en-US": "s", "es-ES": "s"},
        "primary_category": True,
        "age_rating": True,
    },
    "content_rights": "DOES_NOT_USE_THIRD_PARTY_CONTENT",
    "review_detail": {
        "contactFirstName": "A", "contactLastName": "B", "contactEmail": "a@b.c", "contactPhone": "+34600000000",
        "demoAccountRequired": False,
    },
    "price_schedule": True,
    "accessibility": [{"deviceFamily": "IPHONE", "state": "PUBLISHED"}],
    "purchases": [{"name": "pro", "state": "APPROVED"}],
}


def snapshot(**changes):
    s = copy.deepcopy(READY)
    for key, value in changes.items():
        if value is rd:  # sentinel: remove the key, i.e. "could not be read"
            s.pop(key, None)
        else:
            s[key] = value
    return s


def by_label(checks):
    return {c.label: c for c in checks}


class ReadyTests(unittest.TestCase):
    def test_a_complete_snapshot_is_ready(self):
        checks = rd.evaluate(READY)
        self.assertEqual([c.label for c in checks if c.ok is not True], [])
        self.assertTrue(rd.verdict(checks).startswith("READY"))


class BuildTests(unittest.TestCase):
    def test_no_build_attached_points_at_the_latest_processed_one(self):
        c = by_label(rd.evaluate(snapshot(build=None)))["Build attached"]
        self.assertIs(c.ok, False)
        self.assertIn("create_version.py --version 1.0.0 --build 3", c.detail)

    def test_no_build_and_none_processed(self):
        s = snapshot(build=None)
        s.pop("latest_build")
        self.assertIn("no processed build exists yet", by_label(rd.evaluate(s))["Build attached"].detail)

    def test_attached_but_not_valid(self):
        s = snapshot(build={"version": "3", "processingState": "PROCESSING", "usesNonExemptEncryption": None})
        c = by_label(rd.evaluate(s))
        self.assertIs(c["Build attached"].ok, False)
        self.assertNotIn("Export compliance", c)

    def test_export_compliance_unanswered_blocks(self):
        s = snapshot(build={"version": "3", "processingState": "VALID", "usesNonExemptEncryption": None})
        self.assertIs(by_label(rd.evaluate(s))["Export compliance"].ok, False)

    def test_export_compliance_true_still_counts_as_answered(self):
        s = snapshot(build={"version": "3", "processingState": "VALID", "usesNonExemptEncryption": True})
        self.assertIs(by_label(rd.evaluate(s))["Export compliance"].ok, True)


class ListingTests(unittest.TestCase):
    def test_missing_fields_name_the_locales(self):
        s = snapshot()
        s["locales"]["es-ES"]["description"] = "   "
        s["locales"]["en-US"]["supportUrl"] = None
        c = by_label(rd.evaluate(s))
        self.assertEqual(c["Description"].detail, "missing in es-ES")
        self.assertEqual(c["Support URL"].detail, "missing in en-US")
        self.assertIs(c["Keywords"].ok, True)

    def test_whats_new_is_required_only_after_the_first_release(self):
        self.assertNotIn("What's New", by_label(rd.evaluate(READY)))
        s = snapshot(first_release=False)
        self.assertIs(by_label(rd.evaluate(s))["What's New"].ok, False)

    def test_screenshots_only_the_primary_locale_needs_its_own(self):
        c = by_label(rd.evaluate(READY))["Screenshots"]
        self.assertIs(c.ok, True)
        self.assertIn("other locales use these", c.detail)

    def test_screenshots_missing_in_the_primary_locale(self):
        s = snapshot(screenshots={"en-US": {}, "es-ES": {"APP_IPHONE_67": 5}})
        c = by_label(rd.evaluate(s))["Screenshots"]
        self.assertIs(c.ok, False)
        self.assertIn("primary locale en-US", c.detail)

    def test_screenshots_without_a_known_primary_locale_need_at_least_one(self):
        s = snapshot(primary_locale=None, screenshots={"en-US": {}, "es-ES": {}})
        self.assertIs(by_label(rd.evaluate(s))["Screenshots"].ok, False)
        s = snapshot(primary_locale=None, screenshots={"en-US": {}, "es-ES": {"APP_IPHONE_67": 1}})
        self.assertIs(by_label(rd.evaluate(s))["Screenshots"].ok, True)


class AppInfoAndReviewTests(unittest.TestCase):
    def test_review_contact_missing_entirely(self):
        c = by_label(rd.evaluate(snapshot(review_detail=None)))["App Review contact"]
        self.assertIs(c.ok, False)
        self.assertIn("has not been created", c.detail)

    def test_review_contact_lists_missing_fields_and_demo_account(self):
        detail = {"contactFirstName": "A", "contactLastName": "", "contactEmail": "a@b.c",
                  "contactPhone": None, "demoAccountRequired": True, "demoAccountName": "u", "demoAccountPassword": ""}
        c = by_label(rd.evaluate(snapshot(review_detail=detail)))["App Review contact"]
        self.assertEqual(c.detail, "missing: last name, phone, demo account password")

    def test_category_age_rating_rights_and_price(self):
        s = snapshot(content_rights=None, price_schedule=False)
        s["app_info"] = {**READY["app_info"], "primary_category": False, "age_rating": False}
        c = by_label(rd.evaluate(s))
        for label in ("Primary category", "Age rating", "Content rights declaration", "Price schedule"):
            self.assertIs(c[label].ok, False, label)

    def test_privacy_url_per_locale(self):
        s = snapshot()
        s["app_info"]["privacy_urls"]["es-ES"] = ""
        self.assertEqual(by_label(rd.evaluate(s))["Privacy policy URL"].detail, "missing in es-ES")

    def test_privacy_url_cannot_be_read_without_an_app_info(self):
        s = snapshot(app_info={"available": False})
        c = by_label(rd.evaluate(s))["Privacy policy URL"]
        self.assertIsNone(c.ok)


class UnknownIsNeverAPassTests(unittest.TestCase):
    def test_every_unreadable_section_is_reported_as_could_not_check(self):
        for key, label in (
            ("build", "Build attached"), ("locales", "Description"), ("screenshots", "Screenshots"),
            ("copyright", "Copyright"), ("app_info", "Primary category"), ("content_rights", "Content rights declaration"),
            ("review_detail", "App Review contact"), ("price_schedule", "Price schedule"),
        ):
            with self.subTest(section=key):
                checks = rd.evaluate(snapshot(**{key: rd}))
                self.assertIsNone(by_label(checks)[label].ok)
                self.assertFalse(rd.verdict(checks).startswith("READY"))

    def test_verdict_wording(self):
        failing = rd.evaluate(snapshot(build=None, price_schedule=rd))
        self.assertEqual(rd.verdict(failing), "NOT READY: 1 item(s) to fix, 1 could not be checked")
        unclear = rd.evaluate(snapshot(price_schedule=rd))
        self.assertTrue(rd.verdict(unclear).startswith("UNCLEAR"))


class AdvisoryTests(unittest.TestCase):
    def test_advisories_never_make_it_not_ready(self):
        s = snapshot(accessibility=[{"deviceFamily": "IPHONE", "state": "DRAFT"}],
                     purchases=[{"name": "pro", "state": "MISSING_METADATA"}])
        s["app_info"]["subtitles"] = {"en-US": "", "es-ES": ""}
        checks = rd.evaluate(s)
        advisory = [c for c in checks if c.severity == rd.ADVISORY and c.ok is False]
        self.assertEqual({c.label for c in advisory},
                         {"Subtitle", "Accessibility declarations", "In-app purchases and subscriptions"})
        self.assertTrue(rd.verdict(checks).startswith("READY"))

    def test_approved_purchases_pass(self):
        c = by_label(rd.evaluate(READY))["In-app purchases and subscriptions"]
        self.assertIs(c.ok, True)


class RenderTests(unittest.TestCase):
    def test_sections_and_the_not_visible_list(self):
        s = snapshot(build=None, price_schedule=rd)
        text = rd.render("my-app", "1.0.0", "PREPARE_FOR_SUBMISSION", True, rd.evaluate(s))
        self.assertIn("my-app  1.0.0  (PREPARE_FOR_SUBMISSION)  first release", text)
        self.assertIn("Needs fixing before submitting (1)", text)
        self.assertIn("Could not be checked (1)", text)
        self.assertIn("Passed (", text)
        self.assertIn("App Privacy answers", text)
        self.assertIn("NOT READY", text)


if __name__ == "__main__":
    unittest.main()
