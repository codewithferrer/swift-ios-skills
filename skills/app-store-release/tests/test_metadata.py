"""Offline tests for the metadata logic and the app-info client methods.

    python3 -m unittest discover -s tests -v

Nothing here touches the network or needs credentials. The client tests are
skipped when requests/PyJWT are not installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import asc_common  # noqa: E402
import asc_metadata as md  # noqa: E402

try:
    import asc_client  # noqa: E402

    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def loc(locale: str, **attributes) -> dict:
    return {"id": f"id-{locale}", "attributes": {"locale": locale, **attributes}}


class ReadLocalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_reads_fields_and_strips_trailing_newline_and_crlf(self):
        write(self.root, "en-US/description.txt", "Line one\r\nLine two\r\n")
        write(self.root, "en-US/name.txt", "My App\n")
        local = md.read_local(self.root)
        self.assertEqual(local.texts["en-US"], {"description": "Line one\nLine two", "name": "My App"})

    def test_ignores_empty_unknown_and_non_locale_entries_with_a_note(self):
        write(self.root, "en-US/subtitle.txt", "   \n")
        write(self.root, "en-US/release_notes.txt", "x")
        write(self.root, "en-US/name.txt", "Real")
        write(self.root, "screenshots/anything.txt", "x")
        write(self.root, "default/name.txt", "x")
        local = md.read_local(self.root)
        self.assertEqual(local.texts, {"en-US": {"name": "Real"}})
        joined = "\n".join(local.notes)
        self.assertIn("en-US/subtitle.txt: empty", joined)
        self.assertIn("push_whats_new.py", joined)
        self.assertIn("screenshots", joined)

    def test_filters_and_skip_locales(self):
        for locale in ("en-US", "es-ES", "gl"):
            write(self.root, f"{locale}/name.txt", locale)
            write(self.root, f"{locale}/keywords.txt", "k")
        local = md.read_local(self.root, only_locales={"en-US", "gl"}, skip_locales={"gl"}, only_fields={"name"})
        self.assertEqual(local.texts, {"en-US": {"name": "en-US"}})
        self.assertTrue(any("gl: skipped" in n for n in local.notes))

    def test_missing_directory(self):
        with self.assertRaises(FileNotFoundError):
            md.read_local(self.root / "nope")


class LimitTests(unittest.TestCase):
    def test_flags_only_what_is_over(self):
        texts = {"en-US": {"name": "x" * 30, "subtitle": "x" * 31, "promotional_text": "x" * 171, "description": "x" * 4000}}
        problems = md.check_limits(texts)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("subtitle.txt: 31" in p for p in problems))
        self.assertTrue(any("promotional_text.txt: 171" in p for p in problems))

    def test_fields_without_a_limit_are_never_flagged(self):
        self.assertEqual(md.check_limits({"en-US": {"support_url": "https://x/" + "a" * 1000}}), [])


class PlanTests(unittest.TestCase):
    def test_changed_unchanged_and_whitespace_insensitive(self):
        texts = {"en-US": {"description": "New text", "keywords": "a,b"}}
        version_locs = {"en-US": loc("en-US", description="Old text", keywords="a,b\n")}
        plan = md.build_plan(texts, version_locs, info_locs={})
        self.assertEqual([(c.locale, c.field.file, c.kind) for c in plan.changes], [("en-US", "description", "changed")])
        self.assertEqual([(l, s.file) for l, s in plan.unchanged], [("en-US", "keywords")])

    def test_new_locale_on_version(self):
        plan = md.build_plan({"fr-FR": {"description": "Bonjour"}}, {}, info_locs={})
        self.assertEqual(plan.changes[0].kind, "new locale")
        self.assertIsNone(plan.changes[0].old)

    def test_app_level_fields_unavailable_without_editable_app_info(self):
        texts = {"en-US": {"name": "N", "description": "D"}}
        plan = md.build_plan(texts, {"en-US": loc("en-US", description="old")}, info_locs=None)
        self.assertEqual([(l, s.file) for l, s in plan.unavailable], [("en-US", "name")])
        self.assertEqual([c.field.file for c in plan.changes], ["description"])
        self.assertEqual(plan.problems, [])

    def test_new_app_info_locale_without_name_is_a_blocking_problem(self):
        plan = md.build_plan({"fr-FR": {"subtitle": "Sous-titre"}}, {}, info_locs={"en-US": loc("en-US", name="N")})
        self.assertEqual(len(plan.problems), 1)
        self.assertIn("name.txt is required", plan.problems[0])

    def test_new_app_info_locale_with_name_is_fine(self):
        texts = {"fr-FR": {"name": "Nom", "subtitle": "Sous-titre"}}
        plan = md.build_plan(texts, {}, info_locs={})
        self.assertEqual(plan.problems, [])
        self.assertEqual({c.kind for c in plan.changes}, {"new locale"})

    def test_describe_change_short_and_long(self):
        short = md.Change("en-US", md.FIELDS_BY_FILE["name"], "Old", "New", "changed")
        self.assertEqual(md.describe_change(short), '"Old" -> "New"')
        long = md.Change("en-US", md.FIELDS_BY_FILE["description"], "a" * 300, "b" * 400, "changed")
        self.assertEqual(md.describe_change(long), "300 -> 400 characters")


class RemoteTextsTests(unittest.TestCase):
    def test_collects_non_empty_fields_from_both_levels(self):
        version_locs = {
            "en-US": loc("en-US", description="D", keywords="k", promotionalText=None, supportUrl="  "),
            "gl": loc("gl", description="skipped"),
        }
        info_locs = {"en-US": loc("en-US", name="N", subtitle="S", privacyPolicyUrl=None)}
        texts = md.remote_texts(version_locs, info_locs, skip_locales={"gl"})
        self.assertEqual(texts, {"en-US": {"name": "N", "subtitle": "S", "description": "D", "keywords": "k"}})

    def test_no_app_info(self):
        texts = md.remote_texts({"en-US": loc("en-US", description="D")}, None)
        self.assertEqual(texts, {"en-US": {"description": "D"}})


class ConfigTests(unittest.TestCase):
    def test_metadata_path_is_relative_to_the_apps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            apps = root / "asc-apps.json"
            apps.write_text(json.dumps({"a": {"app_id": "1", "metadata_path": "store/metadata"}}))
            app = asc_common.load_app("a", str(apps))
            self.assertEqual(app.metadata_path, root / "store" / "metadata")
            self.assertIsNone(app.release_notes_path)

    def test_unknown_field_name_is_rejected(self):
        class Args:
            fields = "description,bogus"

        with self.assertRaises(asc_common.ConfigError):
            asc_common.selected_fields(Args())


@unittest.skipUnless(HAVE_CLIENT, "requests / PyJWT not installed")
class AppInfoClientTests(unittest.TestCase):
    def make_client(self, responses=None):
        client = object.__new__(asc_client.AppStoreConnectClient)
        client.calls = []

        def fake_request(method, path, **kwargs):
            client.calls.append((method, path, kwargs))
            return (responses or {}).get((method, path), {})

        client._request = fake_request
        return client

    def info(self, info_id, app_store_state, state):
        return {"id": info_id, "attributes": {"appStoreState": app_store_state, "state": state}}

    def test_picks_the_editable_app_info(self):
        data = {"data": [
            self.info("live", "READY_FOR_SALE", "READY_FOR_DISTRIBUTION"),
            self.info("draft", "PREPARE_FOR_SUBMISSION", "PREPARE_FOR_SUBMISSION"),
        ]}
        client = self.make_client({("GET", "/apps/42/appInfos"): data})
        self.assertEqual(client.find_editable_app_info("42")["id"], "draft")

    def test_returns_none_instead_of_falling_back_to_the_live_one(self):
        data = {"data": [self.info("live", "READY_FOR_SALE", "READY_FOR_DISTRIBUTION")]}
        client = self.make_client({("GET", "/apps/42/appInfos"): data})
        self.assertIsNone(client.find_editable_app_info("42"))

    def test_either_state_attribute_marks_an_entry_live(self):
        data = {"data": [self.info("only-new-attr", None, "READY_FOR_DISTRIBUTION")]}
        client = self.make_client({("GET", "/apps/42/appInfos"): data})
        self.assertIsNone(client.find_editable_app_info("42"))

    def test_create_app_info_localization_payload(self):
        client = self.make_client()
        client.create_app_info_localization("info-1", "fr-FR", {"name": "Nom", "subtitle": "Sous-titre"})
        method, path, kwargs = client.calls[0]
        self.assertEqual((method, path), ("POST", "/appInfoLocalizations"))
        body = kwargs["json"]["data"]
        self.assertEqual(body["type"], "appInfoLocalizations")
        self.assertEqual(body["attributes"], {"name": "Nom", "subtitle": "Sous-titre", "locale": "fr-FR"})
        self.assertEqual(body["relationships"]["appInfo"]["data"], {"type": "appInfos", "id": "info-1"})


if __name__ == "__main__":
    unittest.main()
