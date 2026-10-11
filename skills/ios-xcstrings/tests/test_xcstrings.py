"""Offline tests: synthetic catalogs written in the style Xcode uses, no network."""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import catalog as cli  # noqa: E402
import catalog_logic as cl  # noqa: E402
import xcstrings as xc  # noqa: E402

REAL_COMPILE_CHECK = xc.compile_check  # the tests patch the module attribute; this keeps the original


def unit(value, state="translated"):
    return {"stringUnit": {"state": state, "value": value}}


def entry(source=None, comment=None, extraction=None, **locales):
    """entry("Hi", de="Hallo", fr=("Salut", "needs_review"))"""
    out = {}
    if comment:
        out["comment"] = comment
    if extraction:
        out["extractionState"] = extraction
    locs = {}
    if source is not None:
        locs["en"] = unit(source)
    for name, value in locales.items():
        locs[name.replace("_", "-")] = unit(*value) if isinstance(value, tuple) else unit(value)
    if locs:
        out["localizations"] = locs
    return out


def catalog_text(strings, style=xc.Style(), source="en", version="1.0"):
    return xc.dumps({"sourceLanguage": source, "strings": strings, "version": version}, style)


class Workspace(unittest.TestCase):
    def setUp(self):
        # Xcode's compiler is only there on a Mac with Xcode; the tests that need it patch it themselves.
        patcher = mock.patch.object(xc, "compile_check", return_value=(None, "not available in tests"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, strings, name="Localizable.xcstrings", style=xc.Style(), **kw) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(catalog_text(strings, style, **kw), encoding="utf-8")
        return path

    def load(self, strings, **kw) -> xc.Catalog:
        return xc.load(self.write(strings, **kw))


class FormatTests(Workspace):
    def test_blank_and_compact_empty_objects_both_round_trip(self):
        strings = {"": {}, "a": entry("A", de="B")}
        for style in (xc.Style(), xc.Style(empty_object="compact")):
            catalog = self.load(strings, style=style)
            self.assertTrue(catalog.roundtrip_ok, catalog.roundtrip_note)
            self.assertEqual(catalog.style.empty_object, style.empty_object)

    def test_trailing_newline_is_preserved_either_way(self):
        for newline in (True, False):
            catalog = self.load({"a": entry("A")}, style=xc.Style(trailing_newline=newline))
            self.assertTrue(catalog.roundtrip_ok)
            self.assertEqual(catalog.text.endswith("\n"), newline)

    def test_real_xcode_layout_is_reproduced_exactly(self):
        text = ('{\n  "sourceLanguage" : "en",\n  "strings" : {\n    "" : {\n\n    },\n'
                '    "a\\n\\"b\\"" : {\n      "localizations" : {\n        "en" : {\n          "stringUnit" : {\n'
                '            "state" : "translated",\n            "value" : "Zoë – 日本語 \\\\ \\t"\n'
                '          }\n        }\n      }\n    }\n  },\n  "version" : "1.0"\n}')
        path = self.root / "x.xcstrings"
        path.write_text(text, encoding="utf-8")
        catalog = xc.load(path)
        self.assertTrue(catalog.roundtrip_ok, catalog.roundtrip_note)
        self.assertEqual(xc.dumps(catalog.data, catalog.style), text)

    def test_unfamiliar_formatting_is_refused_with_the_first_difference(self):
        path = self.root / "x.xcstrings"
        path.write_text(json.dumps({"sourceLanguage": "en", "strings": {"a": {}}, "version": "1.0"}, indent=4))
        catalog = xc.load(path)
        self.assertFalse(catalog.roundtrip_ok)
        self.assertIn("line", catalog.roundtrip_note)
        with self.assertRaises(xc.CatalogError):
            xc.save(catalog, catalog.data)

    def test_duplicate_keys_are_found_and_block_writing(self):
        text = catalog_text({"a": entry("A")}).replace(
            '    "a" : {', '    "a" : {\n    },\n    "a" : {', 1)
        path = self.root / "x.xcstrings"
        path.write_text(text, encoding="utf-8")
        catalog = xc.load(path)
        self.assertEqual(catalog.duplicates, ["a"])
        self.assertFalse(catalog.writable)
        with self.assertRaises(xc.CatalogError):
            xc.save(catalog, catalog.data)

    def test_things_that_are_not_catalogs_are_clear_errors(self):
        for content in ("not json", "[1, 2]", '{"sourceLanguage": "en"}'):
            path = self.root / "bad.xcstrings"
            path.write_text(content)
            with self.assertRaises(xc.CatalogError):
                xc.load(path)
        with self.assertRaises(xc.CatalogError):
            xc.load(self.root / "missing.xcstrings")

    def test_save_replaces_the_file_keeps_its_mode_and_leaves_no_temp(self):
        path = self.write({"a": entry("A")})
        path.chmod(0o640)
        catalog = xc.load(path)
        data = json.loads(json.dumps(catalog.data))
        data["strings"]["a"]["comment"] = "c"
        xc.save(catalog, data)
        self.assertIn('"comment" : "c"', path.read_text())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual([p.name for p in self.root.iterdir()], ["Localizable.xcstrings"])


class CompileCheckTests(Workspace):
    def test_outcomes(self):
        compile_check = REAL_COMPILE_CHECK
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(compile_check("x")[0], None)
        ok = mock.Mock(returncode=0, stderr="", stdout="")
        bad = mock.Mock(returncode=1, stderr="x.xcstrings: error: nope", stdout="")
        missing = mock.Mock(returncode=72, stderr="xcrun: error: unable to find utility \"xcstringstool\"", stdout="")
        with mock.patch("shutil.which", return_value="/usr/bin/xcrun"):
            with mock.patch("subprocess.run", return_value=ok):
                self.assertEqual(compile_check("x"), (True, ""))
            with mock.patch("subprocess.run", return_value=bad):
                self.assertEqual(compile_check("x"), (False, "x.xcstrings: error: nope"))
            with mock.patch("subprocess.run", return_value=missing):
                self.assertIsNone(compile_check("x")[0])

    def test_save_does_not_write_what_xcode_rejects(self):
        path = self.write({"a": entry("A")})
        catalog = xc.load(path)
        data = json.loads(json.dumps(catalog.data))
        data["strings"]["a"]["comment"] = "c"
        before = path.read_text()
        with mock.patch.object(xc, "compile_check", return_value=(False, "error: unreadable")):
            with self.assertRaises(xc.CatalogError) as caught:
                xc.save(catalog, data)
        self.assertIn("unreadable", str(caught.exception))
        self.assertEqual(path.read_text(), before)
        with mock.patch.object(xc, "compile_check", return_value=(False, "error")):
            xc.save(catalog, data, verify=False)
        self.assertIn('"comment" : "c"', path.read_text())

    def test_audit_reports_a_catalog_xcode_cannot_read(self):
        catalog = self.load({"a": entry("A")})
        with mock.patch.object(xc, "compile_check", return_value=(False, "bad")):
            result = cl.audit(catalog, compile_check=True)
        self.assertEqual(result.errors, 1)
        self.assertEqual(cl.audit(catalog, compile_check=False).errors, 0)


class SpecTests(unittest.TestCase):
    def test_what_counts_as_a_specifier(self):
        kinds = lambda s: [(x.raw, x.position, x.kind) for x in xc.parse_specs(s)]
        self.assertEqual(kinds("%@ has %lld"), [("%@", None, "object"), ("%lld", None, "int64")])
        self.assertEqual(kinds("%2$@ %1$d"), [("%2$@", 2, "object"), ("%1$d", 1, "int32")])
        self.assertEqual(kinds("%.1f%% of %5.2f"), [("%.1f", None, "double"), ("%5.2f", None, "double")])
        self.assertEqual(kinds("100%% sure %#@count@"), [])
        self.assertEqual(xc.substitution_refs("%#@count@ in %#@size@"), ["count", "size"])

    def test_a_percent_followed_by_a_space_is_prose_not_a_placeholder(self):
        self.assertEqual(xc.parse_specs("~1% hombres, 50% of, 100% sure"), [])
        self.assertEqual(xc.percent_lookalikes("~1% hombres, 50% of"), ["% ho", "% o"])
        self.assertEqual(xc.percent_lookalikes("50%% off"), [])
        self.assertEqual([s.raw for s in xc.parse_specs("% 5d")], ["% 5d"])

    def test_matching_and_mismatching_translations(self):
        c = xc.compare_specs
        self.assertEqual(c("%@ scored %lld", "%@ hat %lld"), [])
        self.assertEqual(c("%@ scored %lld", "%2$lld pour %1$@"), [])
        self.assertTrue(any("missing %lld" in p for p in c("Level %lld", "Nivel")))
        self.assertTrue(any("extra %@" in p for p in c("Level", "Nivel %@")))
        self.assertTrue(any("%lld but the source has %@" in p for p in c("%@", "%lld")))
        self.assertTrue(any("%d but the source has %lld" in p for p in c("%lld", "%d")))
        order = c("%@ scored %lld", "%lld pts: %@")
        self.assertTrue(any("different order" in p for p in order))
        self.assertTrue(any("missing substitution %#@n@" in p for p in c("%#@n@ left", "left")))

    def test_plural_forms_may_leave_the_number_out(self):
        self.assertEqual(xc.compare_specs("%lld items", "One item", allow_missing=True), [])
        self.assertTrue(xc.compare_specs("%lld items", "One item", allow_missing=False))

    def test_plural_categories_by_language(self):
        self.assertEqual(xc.plural_categories("en"), ("one", "other"))
        self.assertEqual(xc.plural_categories("pt-BR"), ("one", "other"))
        self.assertEqual(xc.plural_categories("ja"), ("other",))
        self.assertEqual(xc.plural_categories("zh-Hans"), ("other",))
        self.assertEqual(xc.plural_categories("ru"), ("one", "few", "many", "other"))
        self.assertEqual(len(xc.plural_categories("ar")), 6)
        self.assertEqual(xc.plural_categories("xx"), ("other",))

    def test_has_letters(self):
        self.assertFalse(xc.has_letters("%lld"))
        self.assertFalse(xc.has_letters("⌚ 12:30"))
        self.assertTrue(xc.has_letters("%lld pts"))
        self.assertTrue(xc.has_letters("50% off"))


class AuditTests(Workspace):
    def audit(self, strings, locales=None, sources=None, **kw):
        return cl.audit(self.load(strings, **kw), locales, sources)

    def codes(self, result, locale=None):
        return {f.code for f in result.findings if locale is None or f.locale == locale}

    def test_statuses_per_language(self):
        result = self.audit({
            "ok": entry("A", de="A!", fr="A?"),
            "new": entry("B", de=("B", "new")),
            "review": entry("C", de=("C!", "needs_review")),
            "gone": entry("D", fr="D?"),
            "blank": entry("E", de="  "),
        })
        self.assertEqual(result.coverage["de"], {"translated": 1, "new": 1, "needs_review": 1, "missing": 2})
        self.assertEqual(result.coverage["fr"], {"translated": 2, "new": 0, "needs_review": 0, "missing": 3})

    def test_a_language_not_in_the_catalog_is_all_missing_when_asked_for(self):
        result = self.audit({"a": entry("A"), "b": entry("B")}, locales=["de"])
        self.assertEqual(result.coverage["de"]["missing"], 2)

    def test_what_is_not_translatable_is_not_counted(self):
        result = self.audit({
            "keep": entry("Text", de="Text!"),
            "name": {"shouldTranslate": False, "localizations": {"en": unit("Brand")}},
            "num": entry("%lld", de="%lld"),
            "⌚": {},
            "": {},
        })
        self.assertEqual(result.translatable, 1)
        self.assertEqual(result.skipped["should_not_translate"], 1)
        self.assertEqual(result.skipped["no_text"], 3)
        self.assertEqual(result.coverage["de"]["translated"], 1)

    def test_a_key_that_is_its_own_source_is_translatable(self):
        result = self.audit({"Hello there": entry(None, de="Hallo")}, locales=["de", "fr"])
        self.assertEqual(result.coverage["de"]["translated"], 1)
        self.assertEqual(result.coverage["fr"]["missing"], 1)

    def test_placeholder_mismatch_is_an_error(self):
        result = self.audit({"s": entry("%@ scored %lld", de="%@ hat Punkte", fr="%2$lld par %1$@")})
        self.assertEqual(result.errors, 1)
        finding = next(f for f in result.findings if f.code == "placeholder_mismatch")
        self.assertEqual(finding.locale, "de")
        self.assertIn("missing %lld", finding.details[0])

    def test_literal_percent_in_a_translation_is_a_warning_not_a_mismatch(self):
        result = self.audit({"s": entry("About 1% of men", de="Etwa 1% der Männer")})
        self.assertEqual(result.errors, 0)
        self.assertIn("percent_space", self.codes(result, "de"))

    def test_identifier_key_without_source_value(self):
        result = self.audit({"home_title_key": entry(None, de="Start"), "x-ray": entry(None, de="Röntgen"),
                             "Plain words": entry(None, de="Einfach")})
        finding = next(f for f in result.findings if f.code == "no_source_value")
        self.assertEqual(finding.details, ["home_title_key"])

    def test_whitespace_and_line_breaks(self):
        result = self.audit({
            "a": entry("Line one\nLine two", de="Zeile eins Zeile zwei"),
            "b": entry("Tap ", de="Tippen"),
            "c": entry("Fine\n", de="Gut\n"),
        })
        self.assertEqual({"newline_differs", "whitespace_differs"}, self.codes(result, "de") - {"identical_to_source"})
        newline = next(f for f in result.findings if f.code == "newline_differs")
        self.assertEqual(len(newline.details), 1)

    def test_identical_is_flagged_only_when_other_latin_languages_translated_it(self):
        result = self.audit({
            "name": entry("Chess board", de="Chess board", es="Chess board", fr="Chess board"),
            "miss": entry("Settings page", de="Settings page", es="Página de ajustes", fr="Page de réglages"),
        })
        flagged = [d.split("  ")[0] for f in result.findings if f.code == "identical_to_source" for d in f.details]
        self.assertEqual(flagged, ["miss"])

    def test_plural_variations(self):
        plural = lambda **forms: {"variations": {"plural": {k: unit(v) for k, v in forms.items()}}}
        strings = {"n": {"localizations": {
            "en": plural(one="%lld item", other="%lld items"),
            "de": plural(one="Ein Element", other="%lld Elemente"),
            "ru": plural(one="%lld элемент", other="%lld элементов"),
            "ja": plural(other="%lld 個"),
        }}}
        result = self.audit(strings, locales=["de", "ru", "ja"])
        self.assertEqual(self.codes(result, "de"), set())
        self.assertIn("plural_incomplete", self.codes(result, "ru"))
        self.assertEqual(self.codes(result, "ja"), set())

    def test_stale_keys_in_use_are_kept_and_unused_ones_are_reported(self):
        strings = {
            "used literal": entry("used literal", extraction="stale", de="benutzt"),
            "Hello %@": entry("Hello %@", extraction="stale", de="Hallo %@"),
            "dead": entry("dead", extraction="stale", de="tot"),
            "live": entry("live", de="lebendig"),
        }
        sources = 'Text("used literal"); Text("Hello \\(name)")'
        result = self.audit(strings, sources=sources)
        self.assertEqual(result.skipped["stale_unreferenced"], 1)
        self.assertEqual(result.translatable, 3)
        unused = next(f for f in result.findings if f.code == "stale_unreferenced")
        self.assertEqual(unused.details, ["dead"])
        self.assertIn("stale_in_use", self.codes(result))
        without_scan = self.audit(strings)
        self.assertEqual(without_scan.translatable, 4)

    def test_duplicate_keys_are_an_error(self):
        text = catalog_text({"a": entry("A")}).replace('    "a" : {', '    "a" : {\n    },\n    "a" : {', 1)
        path = self.root / "dup.xcstrings"
        path.write_text(text, encoding="utf-8")
        result = cl.audit(xc.load(path))
        self.assertEqual(result.errors, 1)
        self.assertFalse(result.writable)

    def test_complex_sources_are_counted_but_not_written(self):
        device = {"variations": {"device": {"iphone": unit("Tap"), "mac": unit("Click")}}}
        result = self.audit({"c": {"localizations": {"en": device}}}, locales=["de"])
        self.assertIn("complex_source", self.codes(result))


class TodoTests(Workspace):
    def test_lists_missing_new_and_review_with_context(self):
        catalog = self.load({
            "m": entry("Missing %@", comment="Shown on the home screen"),
            "n": entry("New", de=("New", "new")),
            "r": entry("Review", de=("Alt", "needs_review")),
            "t": entry("Done", de="Fertig"),
        })
        work = cl.todo(catalog, "de")
        self.assertEqual([i["key"] for i in work["items"]], ["m", "n", "r"])
        by_key = {i["key"]: i for i in work["items"]}
        self.assertEqual(by_key["m"]["reason"], "missing")
        self.assertEqual(by_key["m"]["comment"], "Shown on the home screen")
        self.assertEqual(by_key["m"]["placeholders"], ["%@"])
        self.assertEqual(by_key["r"]["current"], "Alt")
        self.assertEqual(cl.todo(catalog, "de", include=("missing",))["total"], 1)

    def test_a_new_language_lists_everything_translatable(self):
        catalog = self.load({"a": entry("A"), "b": entry("B"), "n": entry("%lld")})
        self.assertEqual(cl.todo(catalog, "ja")["total"], 2)

    def test_source_language_is_refused(self):
        with self.assertRaises(xc.CatalogError):
            cl.todo(self.load({"a": entry("A")}), "en")

    def test_plural_item_names_the_categories_the_language_needs(self):
        strings = {"n": {"localizations": {"en": {"variations": {"plural": {
            "one": unit("%lld item"), "other": unit("%lld items")}}}}}}
        work = cl.todo(self.load(strings), "ru")
        item = work["items"][0]
        self.assertEqual(item["source"], {"one": "%lld item", "other": "%lld items"})
        self.assertEqual(item["plural_categories"], ["one", "few", "many", "other"])

    def test_examples_prefer_keys_that_share_a_prefix(self):
        catalog = self.load({
            "home_title": entry("Title", de="Titel"),
            "home_play": entry("Play", de="Spielen"),
            "settings_sound": entry("Sound", de="Ton"),
            "home_quit": entry("Quit"),
        })
        work = cl.todo(catalog, "de", examples=1)
        self.assertEqual(work["examples"][0]["key"], "home_title")

    def test_reference_languages_ride_along_only_when_translated(self):
        catalog = self.load({"a": entry("Challenge", es="Reto"), "b": entry("Other", es=("Otro", "new")),
                             "c": entry("Third")})
        items = {i["key"]: i for i in cl.todo(catalog, "de", reference=["es"])["items"]}
        self.assertEqual(items["a"]["reference"], {"es": "Reto"})
        self.assertNotIn("reference", items["b"])
        self.assertNotIn("reference", items["c"])

    def test_limit_keeps_the_total(self):
        catalog = self.load({f"k{i}": entry(f"Word {i}") for i in range(5)})
        work = cl.todo(catalog, "de", limit=2)
        self.assertEqual((work["total"], len(work["items"])), (5, 2))

    def test_complex_keys_are_reported_not_listed(self):
        device = {"variations": {"device": {"iphone": unit("Tap")}}}
        catalog = self.load({"c": {"localizations": {"en": device}}, "a": entry("A")})
        work = cl.todo(catalog, "de")
        self.assertEqual([i["key"] for i in work["items"]], ["a"])
        self.assertEqual(work["not_handled"]["keys"], ["c"])


class ApplyTests(Workspace):
    def apply(self, strings, translations, language="de", state="needs_review", overwrite=False, write=True, **kw):
        path = self.write(strings, **kw)
        catalog = xc.load(path)
        plan = cl.plan_apply(catalog, {"language": language, "translations": translations}, state, overwrite)
        if write and not plan.problems:
            xc.save(catalog, cl.apply_plan(catalog, plan))
        return plan, path

    def test_fills_a_missing_translation_and_nothing_else(self):
        strings = {f"k{i}": entry(f"Word {i}", fr=f"Mot {i}") for i in range(30)}
        plan, path = self.apply(strings, {"k7": "Wort 7"})
        self.assertEqual(plan.problems, [])
        before = catalog_text(strings).splitlines()
        after = path.read_text().splitlines()
        added = [l for l in after if l not in before]
        self.assertEqual(len(after) - len(before), 6, added)
        data = json.loads(path.read_text())
        self.assertEqual(data["strings"]["k7"]["localizations"]["de"],
                         {"stringUnit": {"state": "needs_review", "value": "Wort 7"}})
        self.assertEqual(list(data["strings"]["k7"]["localizations"]), ["de", "en", "fr"])
        self.assertTrue(xc.load(path).roundtrip_ok)

    def test_the_state_is_recorded(self):
        _, path = self.apply({"a": entry("A")}, {"a": "B"}, state="translated")
        self.assertEqual(json.loads(path.read_text())["strings"]["a"]["localizations"]["de"]["stringUnit"]["state"],
                         "translated")

    def test_an_unsorted_catalog_gets_the_new_language_appended(self):
        strings = {"a": {"localizations": {"en": unit("A"), "fr": unit("B"), "ar": unit("C")}}}
        _, path = self.apply(strings, {"a": "D"})
        self.assertEqual(list(json.loads(path.read_text())["strings"]["a"]["localizations"]),
                         ["en", "fr", "ar", "de"])

    def test_an_entry_without_localizations_gets_them_in_key_order(self):
        strings = {"a": {"comment": "c", "shouldTranslate": True}}
        _, path = self.apply(strings, {"a": "Wort"}, **{})
        self.assertEqual(list(json.loads(path.read_text())["strings"]["a"]), ["comment", "localizations", "shouldTranslate"])

    def test_problems_block_everything(self):
        strings = {"a": entry("%@ scored %lld"), "b": entry("B"), "c": {"shouldTranslate": False}}
        plan, path = self.apply(strings, {"a": "%@ hat", "b": "Be", "c": "Cee", "zz": "?"})
        text = "\n".join(plan.problems)
        self.assertIn("missing %lld", text)
        self.assertIn("no such key", text)
        self.assertIn("Don't translate", text)
        self.assertEqual(path.read_text(), catalog_text(strings))

    def test_translated_values_are_protected_unless_overwrite(self):
        strings = {"a": entry("A", de="Alt")}
        plan, path = self.apply(strings, {"a": "Neu"})
        self.assertIn("--overwrite", plan.problems[0])
        plan, path = self.apply(strings, {"a": "Neu"}, overwrite=True)
        self.assertEqual(plan.problems, [])
        self.assertEqual(json.loads(path.read_text())["strings"]["a"]["localizations"]["de"]["stringUnit"]["value"], "Neu")

    def test_needs_review_can_be_replaced_without_overwrite(self):
        plan, _ = self.apply({"a": entry("A", de=("Alt", "needs_review"))}, {"a": "Neu"})
        self.assertEqual((plan.problems, len(plan.changes)), ([], 1))

    def test_giving_the_same_value_again_changes_nothing(self):
        plan, _ = self.apply({"a": entry("A", de=("Alt", "needs_review"))}, {"a": "Alt"}, write=False)
        self.assertEqual(len(plan.changes), 1)
        plan, _ = self.apply({"a": entry("A", de="Alt")}, {"a": "Alt"}, state="translated", write=False)
        self.assertEqual((plan.changes, plan.unchanged), ([], ["a"]))

    def test_plural_translation_needs_the_languages_categories(self):
        strings = {"n": {"localizations": {"en": {"variations": {"plural": {
            "one": unit("%lld item"), "other": unit("%lld items")}}}}}}
        plan, _ = self.apply(strings, {"n": {"one": "Ein Element", "other": "%lld Elemente"}}, write=False)
        self.assertEqual(plan.problems, [])
        plan, _ = self.apply(strings, {"n": {"other": "%lld Elemente"}}, write=False)
        self.assertIn("missing one", plan.problems[0])
        plan, _ = self.apply(strings, {"n": {"one": "x", "few": "y", "many": "z", "other": "%lld w"}}, language="ru")
        self.assertEqual(plan.problems, [])

    def test_plural_write_is_sorted_and_keeps_the_shape(self):
        strings = {"n": {"localizations": {"en": {"variations": {"plural": {
            "one": unit("%lld item"), "other": unit("%lld items")}}}}}}
        _, path = self.apply(strings, {"n": {"other": "%lld Elemente", "one": "Ein Element"}})
        de = json.loads(path.read_text())["strings"]["n"]["localizations"]["de"]
        self.assertEqual(list(de["variations"]["plural"]), ["one", "other"])
        self.assertEqual(de["variations"]["plural"]["one"]["stringUnit"]["state"], "needs_review")

    def test_the_source_language_and_bad_files_are_refused(self):
        catalog = self.load({"a": entry("A")})
        for payload, language in (({"language": "en", "translations": {"a": "x"}}, None),
                                  ({"translations": {"a": "x"}}, None),
                                  ({"language": "de", "translations": {}}, None),
                                  ({"language": "de", "translations": {"a": ""}}, None)):
            plan = cl.plan_apply(catalog, payload, language=language)
            self.assertTrue(plan.problems, payload)

    def test_a_catalog_that_cannot_be_reproduced_is_not_written(self):
        path = self.root / "x.xcstrings"
        path.write_text(json.dumps({"sourceLanguage": "en", "strings": {"a": {}}, "version": "1.0"}, indent=4))
        plan = cl.plan_apply(xc.load(path), {"language": "de", "translations": {"a": "b"}})
        self.assertIn("cannot be written safely", plan.problems[0])


class CliTests(Workspace):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["catalog.py", *map(str, argv)]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main()
        return code, out.getvalue(), err.getvalue()

    def test_audit_text_and_exit_codes(self):
        clean = self.write({"a": entry("A", de="B")}, name="clean/Localizable.xcstrings")
        code, out, _ = self.run_cli("audit", clean)
        self.assertEqual(code, 0)
        self.assertIn("100.0%", out)
        broken = self.write({"a": entry("%@", de="x")}, name="broken/Localizable.xcstrings")
        code, out, _ = self.run_cli("audit", broken)
        self.assertEqual(code, 1)
        self.assertIn("placeholder_mismatch", out)

    def test_strict_fails_on_warnings(self):
        path = self.write({"a": entry("A", de="B"), "b": entry("C")})
        self.assertEqual(self.run_cli("audit", path)[0], 0)
        self.assertEqual(self.run_cli("audit", path, "--strict")[0], 1)

    def test_discovery_skips_build_and_worktree_folders(self):
        self.write({"a": entry("A")}, name="App/Localizable.xcstrings")
        self.write({"a": entry("A")}, name=".claude/worktrees/x/Localizable.xcstrings")
        self.write({"a": entry("A")}, name="DerivedData/Localizable.xcstrings")
        code, out, _ = self.run_cli("audit", "--root", self.root, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 1)

    def test_todo_writes_json_and_counts_on_stderr(self):
        path = self.write({"a": entry("A"), "b": entry("B", de="C")})
        code, out, err = self.run_cli("todo", "--catalog", path, "--locale", "de")
        self.assertEqual(code, 0)
        self.assertEqual([i["key"] for i in json.loads(out)["items"]], ["a"])
        self.assertIn("1 key(s) to translate into de", err)

    def test_several_catalogs_need_an_explicit_choice(self):
        self.write({"a": entry("A")}, name="one/Localizable.xcstrings")
        self.write({"a": entry("A")}, name="two/Localizable.xcstrings")
        code, _, err = self.run_cli("todo", "--root", self.root, "--locale", "de")
        self.assertEqual(code, 1)
        self.assertIn("--catalog", err)

    def test_apply_previews_then_writes(self):
        path = self.write({"a": entry("A"), "b": entry("B")})
        payload = self.root / "de.json"
        payload.write_text(json.dumps({"language": "de", "translations": {"a": "Ah"}}))
        before = path.read_text()
        code, out, _ = self.run_cli("apply", "--catalog", path, "--file", payload)
        self.assertEqual(code, 0)
        self.assertIn("Preview only", out)
        self.assertEqual(path.read_text(), before)
        code, out, _ = self.run_cli("apply", "--catalog", path, "--file", payload, "--write")
        self.assertEqual(code, 0, out)
        self.assertIn("Written: 6 line(s) added", out)
        self.assertIn("de now: 0 translated, 1 need review", out)

    def test_apply_with_problems_exits_1_and_changes_nothing(self):
        path = self.write({"a": entry("%@")})
        payload = self.root / "de.json"
        payload.write_text(json.dumps({"language": "de", "translations": {"a": "x"}}))
        before = path.read_text()
        code, _, err = self.run_cli("apply", "--catalog", path, "--file", payload, "--write")
        self.assertEqual(code, 1)
        self.assertIn("missing %@", err)
        self.assertEqual(path.read_text(), before)


if __name__ == "__main__":
    unittest.main()
