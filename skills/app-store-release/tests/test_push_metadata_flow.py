"""Offline test of push_metadata's apply phase for a brand-new locale.

App Store Connect creates the app-info localization by itself, copying the
primary language's name and subtitle, as soon as a version localization for a
new locale exists. The push must notice that instead of trying to create it
(which answers 409) and must still fix whatever differs.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import push_metadata  # noqa: E402


class FakeError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeClient:
    def __init__(self):
        self.calls = []
        self.version_locs = {"en-US": {"id": "v-en-US", "attributes": {"locale": "en-US", "description": "d"}}}
        self.info_locs = {"en-US": {"id": "i-en-US", "attributes": {"locale": "en-US", "name": "Brand", "subtitle": "Primary subtitle"}}}

    def find_version(self, app_id, version):
        return {"id": "v1", "attributes": {"appVersionState": "PREPARE_FOR_SUBMISSION"}}

    def list_localizations(self, version_id):
        return dict(self.version_locs)

    def find_editable_app_info(self, app_id):
        return {"id": "info1", "attributes": {}}

    def list_app_info_localizations(self, info_id):
        self.calls.append(("list_info",))
        return dict(self.info_locs)

    def create_localization(self, version_id, locale, attrs):
        self.calls.append(("create_version", locale, dict(attrs)))
        self.version_locs[locale] = {"id": f"v-{locale}", "attributes": {"locale": locale, **attrs}}
        # What App Store Connect does as a side effect:
        self.info_locs[locale] = {"id": f"i-{locale}",
                                  "attributes": {"locale": locale, "name": "Brand", "subtitle": "Primary subtitle"}}

    def update_localization(self, loc_id, attrs):
        self.calls.append(("update_version", loc_id, dict(attrs)))

    def update_app_info_localization(self, loc_id, attrs):
        self.calls.append(("update_info", loc_id, dict(attrs)))

    def create_app_info_localization(self, info_id, locale, attrs):
        self.calls.append(("create_info", locale, dict(attrs)))
        if locale in self.info_locs:
            raise FakeError("409 duplicate", status_code=409)


class NewLocaleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "asc-apps.json").write_text('{"app": {"app_id": "1", "metadata_path": "meta"}}')

    def write(self, locale, **fields):
        folder = self.root / "meta" / locale
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in fields.items():
            (folder / f"{name}.txt").write_text(text + "\n", encoding="utf-8")

    def push(self, client):
        module = types.SimpleNamespace(AppStoreConnectClient=lambda: client, AppStoreConnectError=FakeError)
        argv = ["push_metadata.py", "--apps-file", str(self.root / "asc-apps.json"), "--app", "app",
                "--version", "1.0.0", "--yes"]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(push_metadata, "load_client_module", return_value=module), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = push_metadata.main()
        return code, out.getvalue(), err.getvalue()

    def test_name_and_subtitle_that_differ_are_updated_not_created(self):
        self.write("de-DE", name="Brand: Wecker mit Mission", subtitle="Rätsel für Langschläfer",
                   description="Beschreibung", keywords="morgen,aufstehen")
        client = FakeClient()
        code, out, err = self.push(client)
        self.assertEqual(code, 0, err)
        kinds = [c[0] for c in client.calls]
        self.assertIn("create_version", kinds)
        self.assertNotIn("create_info", kinds)
        update = next(c for c in client.calls if c[0] == "update_info")
        self.assertEqual(update[1], "i-de-DE")
        self.assertEqual(update[2], {"name": "Brand: Wecker mit Mission", "subtitle": "Rätsel für Langschläfer"})
        self.assertIn("de-DE: wrote", out)

    def test_values_that_already_match_the_copied_ones_are_not_rewritten(self):
        self.write("en-GB", name="Brand", subtitle="Primary subtitle", description="Description", keywords="a,b")
        client = FakeClient()
        code, out, err = self.push(client)
        self.assertEqual(code, 0, err)
        kinds = [c[0] for c in client.calls]
        self.assertNotIn("update_info", kinds)
        self.assertNotIn("create_info", kinds)
        self.assertIn("name, subtitle already matched", out)

    def test_only_the_differing_attribute_is_sent(self):
        self.write("fr-FR", name="Brand", subtitle="Sous-titre", description="Description", keywords="a,b")
        client = FakeClient()
        self.push(client)
        update = next(c for c in client.calls if c[0] == "update_info")
        self.assertEqual(update[2], {"subtitle": "Sous-titre"})

    def test_existing_locale_still_updates_in_place(self):
        self.write("en-US", name="Brand", subtitle="A new subtitle", description="d")
        client = FakeClient()
        code, out, err = self.push(client)
        self.assertEqual(code, 0, err)
        self.assertEqual([c for c in client.calls if c[0] == "update_info"][0][2], {"subtitle": "A new subtitle"})
        self.assertNotIn("create_version", [c[0] for c in client.calls])


if __name__ == "__main__":
    unittest.main()
