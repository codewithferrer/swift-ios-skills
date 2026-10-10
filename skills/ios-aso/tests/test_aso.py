"""Offline tests for the ASO rules and the keyword packer.

    python3 -m unittest discover -s tests -v

No network. Each rule is pinned because the point of the scripts is to count
and compare correctly where hand-counting goes wrong.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import aso_logic as al  # noqa: E402

TABLE = al.load_storefronts(SCRIPTS.parent / "references" / "storefront-locales.json")


def messages(findings, severity=None, where=None):
    return [f.message for f in findings
            if (severity is None or f.severity == severity) and (where is None or where in f.where.split(", "))]


class TextHelperTests(unittest.TestCase):
    def test_words_and_cjk_terms(self):
        self.assertEqual(al.words("Wake-up, Alarm Challenge!"), ["wake", "up", "alarm", "challenge"])
        self.assertEqual(al.keyword_tokens("clock,real estate,連続記録"), ["clock", "real", "estate", "連続記録"])
        self.assertTrue(al.has_cjk("チャレンジ"))
        self.assertFalse(al.has_cjk("challenge"))

    def test_plural_variants(self):
        self.assertEqual(al.plural_variant("alarm", {"alarms", "clock"}), "alarms")
        self.assertEqual(al.plural_variant("alarms", {"alarm"}), "alarm")
        self.assertEqual(al.plural_variant("reloj", {"relojes"}), "relojes")
        self.assertIsNone(al.plural_variant("clock", {"morning"}))
        self.assertIsNone(al.plural_variant("チャレンジ", {"チャレンジs"}))


class LocaleAuditTests(unittest.TestCase):
    def fields(self, **kw):
        base = {"name": "Brand: Alarm Challenge", "subtitle": "Wake up on the first try",
                "keywords": "clock,morning,qr,nfc", "promotional_text": "Promo"}
        base.update(kw)
        return base

    def test_clean_listing_has_only_the_free_budget_opportunity(self):
        found = al.audit_locale("en-US", self.fields(keywords="a" * 5 + ",morning,sleeper,routine,habit,widget,siri,focus,steps,math,shake,qr,nfc"))
        self.assertEqual([f for f in found if f.severity == "fix"], [])

    def test_spaces_around_commas_empty_terms_and_repeats(self):
        found = al.audit_locale("en-US", self.fields(keywords="clock, morning ,clock,,qr"))
        fixes = messages(found, "fix")
        self.assertTrue(any("spaces around commas" in m for m in fixes))
        self.assertTrue(any("empty term" in m for m in fixes))
        self.assertTrue(any("repeated within the field: clock" in m for m in fixes))

    def test_words_already_in_name_or_subtitle(self):
        found = al.audit_locale("en-US", self.fields(keywords="alarm,wake,clock"))
        text = " ".join(messages(found, "fix"))
        self.assertIn("alarm", text)
        self.assertIn("wake", text)
        self.assertNotIn("clock,", text)

    def test_missing_name_is_called_out_instead_of_silently_skipped(self):
        found = al.audit_locale("en-US", {"subtitle": "S", "keywords": "clock"})
        self.assertTrue(any("no name.txt" in m for m in messages(found, "check")))
        found = al.audit_locale("en-US", {"name": "N", "subtitle": "S", "keywords": "clock"})
        self.assertFalse(any("no name.txt" in m for m in messages(found, "check")))

    def test_cjk_term_already_in_name(self):
        found = al.audit_locale("ja", {"name": "記憶チャレンジ", "subtitle": "", "keywords": "チャレンジ,記憶チャレンジ"})
        self.assertFalse(any("already indexed" in m for m in messages(found, "fix") if "チャレンジ,記憶" in m))
        self.assertTrue(any("記憶チャレンジ" in m for m in messages(found, "fix")))

    def test_over_limit_values_are_fixes(self):
        found = al.audit_locale("en-US", {"name": "n" * 31, "subtitle": "s" * 31, "keywords": "k" * 101,
                                          "promotional_text": "p" * 171})
        fixes = " | ".join(messages(found, "fix"))
        for needle in ("name is 31", "subtitle is 31", "keywords are 101", "promotional text is 171"):
            self.assertIn(needle, fixes)

    def test_bytes_note_only_when_characters_fit_but_bytes_do_not(self):
        ja = "あ" * 40  # 40 characters, 120 bytes
        found = al.audit_locale("ja", {"name": "N", "subtitle": "S", "keywords": ja})
        self.assertTrue(any("UTF-8 bytes" in m for m in messages(found, "check")))
        found = al.audit_locale("en-US", {"name": "N", "subtitle": "S", "keywords": "abc"})
        self.assertFalse(any("UTF-8 bytes" in m for m in messages(found, "check")))

    def test_unused_budget_and_missing_fields(self):
        found = al.audit_locale("en-US", {"name": "N", "subtitle": "", "keywords": "a,b"})
        opp = " | ".join(messages(found, "opportunity"))
        self.assertIn("no subtitle", opp)
        self.assertIn("keywords use 3 of 100", opp)
        self.assertIn("no promotional text", opp)

    def test_subtitle_repeating_the_name(self):
        found = al.audit_locale("en-US", {"name": "Pixel Colors", "subtitle": "Colors daily", "keywords": "x"})
        self.assertTrue(any("repeats words already in the name (colors)" in m for m in messages(found, "opportunity")))

    def test_claims_generic_words_and_category(self):
        found = al.audit_locale("en-US", {"name": "Best Alarm", "subtitle": "Free", "keywords": "app,games,clock"},
                                category="Games")
        checks = " | ".join(messages(found, "check"))
        self.assertIn("best", checks)
        self.assertIn("free", checks)
        self.assertIn("app", checks)
        self.assertIn("category word(s) games", checks)

    def test_possible_plurals(self):
        found = al.audit_locale("en-US", {"name": "N", "subtitle": "S", "keywords": "alarm,alarms,clock"})
        self.assertTrue(any("alarm/alarms" in m or "alarms/alarm" in m for m in messages(found, "check")))


class StorefrontAuditTests(unittest.TestCase):
    def test_shared_words_are_grouped_by_word_and_names_do_not_count(self):
        listings = {
            "en-US": {"name": "Brand", "subtitle": "Wake", "keywords": "clock,morning,qr"},
            "es-MX": {"name": "Brand", "subtitle": "Despierta", "keywords": "reloj,clock,qr,mañana"},
        }
        found = al.audit_storefront("US", TABLE["US"], listings)
        msg = " ".join(messages(found, "check"))
        self.assertIn("clock (en-US, es-MX)", msg)
        self.assertIn("qr (en-US, es-MX)", msg)
        self.assertNotIn("brand", msg)

    def test_identical_keyword_fields_for_one_storefront(self):
        listings = {"en-US": {"keywords": "a,b,c"}, "es-MX": {"keywords": "A,B,C"}}
        found = al.audit_storefront("US", TABLE["US"], listings)
        self.assertTrue(any("identical keyword fields" in m for m in messages(found, "fix")))

    def test_locales_that_do_not_share_a_storefront_do_not_clash(self):
        listings = {"en-US": {"keywords": "clock"}, "de-DE": {"keywords": "clock"}}
        self.assertEqual(messages(al.audit_storefront("US", TABLE["US"], listings), "check"), [])

    def test_default_locale_missing(self):
        found = al.audit_storefront("MX", TABLE["MX"], {"en-US": {"keywords": "a"}})
        self.assertTrue(any("default locale es-MX has no localization" in m for m in messages(found)))

    def test_missing_additional_locales_are_aggregated(self):
        listings = {"en-US": {"keywords": "a"}, "es-ES": {"keywords": "b"}, "de-DE": {"keywords": "c"}}
        found = al.missing_additional_locales(listings, TABLE, ["US", "ES", "DE"])
        by_locale = {f.message.split(" ")[0]: f for f in found}
        self.assertIn("en-GB", by_locale)
        self.assertEqual(set(by_locale["en-GB"].where.split(",")), {"ES", "DE"})
        self.assertIn("es-MX", by_locale)
        self.assertEqual(by_locale["es-MX"].where, "US")

    def test_default_storefronts_follow_the_locales_present(self):
        codes = al.default_storefronts({"en-US": {}, "es-ES": {}, "ja": {}}, TABLE)
        self.assertEqual(set(codes), {"US", "ES", "JP"})


class MergeTests(unittest.TestCase):
    def test_identical_messages_name_every_locale_once(self):
        merged = al.merge_identical([
            al.Finding("opportunity", "en-US", "no promotional text"),
            al.Finding("opportunity", "es-ES", "no promotional text"),
            al.Finding("fix", "en-US", "something else"),
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].where, "en-US, es-ES")


class PackTests(unittest.TestCase):
    def test_priority_order_and_budget(self):
        r = al.pack_keywords(["aaaa", "bbbb", "cccc"], limit=9)
        self.assertEqual(r.field, "aaaa,bbbb")
        self.assertEqual(r.did_not_fit, ["cccc"])
        self.assertLessEqual(r.length, 9)

    def test_skips_words_indexed_elsewhere_and_explains_why(self):
        r = al.pack_keywords(["wake", "clock", "alarms"], already_indexed={"wake", "alarm"})
        self.assertEqual(r.chosen, ["clock"])
        reasons = dict(r.skipped)
        self.assertEqual(reasons["wake"], "already indexed")
        self.assertIn("singular/plural of 'alarm'", reasons["alarms"])

    def test_phrases_are_split_by_default_and_deduplicated(self):
        r = al.pack_keywords(["heavy sleeper", "sleeper"])
        self.assertEqual(r.chosen, ["heavy", "sleeper"])

    def test_keep_phrases(self):
        r = al.pack_keywords(["heavy sleeper", "clock"], split_phrases=False)
        self.assertEqual(r.field, "heavy sleeper,clock")

    def test_cjk_terms_are_kept_whole(self):
        r = al.pack_keywords(["連続記録", "デイリー"], already_indexed={"デイリー"})
        self.assertEqual(r.chosen, ["連続記録"])

    def test_a_later_shorter_word_can_still_fit(self):
        r = al.pack_keywords(["aaaaa", "bbbbbbbbbb", "c"], limit=8)
        self.assertEqual(r.field, "aaaaa,c")
        self.assertEqual(r.did_not_fit, ["bbbbbbbbbb"])

    def test_field_never_exceeds_the_limit(self):
        words_ = [f"word{i:03d}" for i in range(60)]
        r = al.pack_keywords(words_)
        self.assertLessEqual(len(r.field), 100)


class LoadAndCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_load_listings_skips_non_locale_folders_and_blank_files(self):
        self.write("en-US/name.txt", "App\n")
        self.write("en-US/keywords.txt", "   \n")
        self.write("screenshots/name.txt", "x")
        self.write("review_information/first_name.txt", "x")
        listings = al.load_listings(self.root)
        self.assertEqual(listings, {"en-US": {"name": "App"}})

    def run_script(self, name, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / name), *args], capture_output=True, text=True)

    def test_audit_cli(self):
        self.write("en-US/name.txt", "Brand")
        self.write("en-US/subtitle.txt", "Wake up")
        self.write("en-US/keywords.txt", "wake,clock, morning")
        result = self.run_script("aso_audit.py", "--dir", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Storefronts audited: US", result.stdout)
        self.assertIn("FIX", result.stdout)
        self.assertIn("spaces around commas", result.stdout)

    def test_audit_cli_unknown_storefront_and_empty_dir(self):
        self.write("en-US/name.txt", "A")
        bad = self.run_script("aso_audit.py", "--dir", str(self.root), "--storefronts", "ZZ")
        self.assertEqual(bad.returncode, 1)
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        self.assertEqual(self.run_script("aso_audit.py", "--dir", empty.name).returncode, 1)

    def test_pack_cli(self):
        result = self.run_script("keyword_pack.py", "--candidates", "wake,clock,alarms,morning",
                                 "--name", "Alarm Challenge", "--subtitle", "Wake up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clock,morning", result.stdout)
        self.assertIn("alarms: singular/plural of 'alarm'", result.stdout)
        self.assertEqual(self.run_script("keyword_pack.py").returncode, 1)


if __name__ == "__main__":
    unittest.main()
