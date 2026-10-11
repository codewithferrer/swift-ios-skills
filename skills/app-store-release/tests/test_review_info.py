"""Offline tests for the copyright line and the App Review information.

    python3 -m unittest discover -s tests -v

The point of these tests: the right values reach the right attributes, bad
input is refused before anything is sent, and the demo account password is
never printed or written back into a file.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import asc_metadata as md  # noqa: E402
import pull_metadata  # noqa: E402
import push_metadata  # noqa: E402

PASSWORD = "S3cret-demo-pw"


class FakeError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FakeClient:
    def __init__(self, copyright=None, detail=None):
        self.calls = []
        self.copyright = copyright
        self.detail = detail

    def find_version(self, app_id, version):
        return {"id": "v1", "attributes": {"appVersionState": "PREPARE_FOR_SUBMISSION", "copyright": self.copyright}}

    def list_localizations(self, version_id):
        self.calls.append(("list_localizations",))
        return {}

    def find_editable_app_info(self, app_id):
        return None

    def list_app_infos(self, app_id):
        return []

    def get_review_detail(self, version_id):
        return self.detail

    def update_version(self, version_id, attributes):
        self.calls.append(("update_version", dict(attributes)))

    def create_review_detail(self, version_id, attributes):
        self.calls.append(("create_review", dict(attributes)))
        return {"id": "rd1"}

    def update_review_detail(self, detail_id, attributes):
        self.calls.append(("update_review", detail_id, dict(attributes)))


class LogicTests(unittest.TestCase):
    def test_read_global_ignores_blank_and_unknown_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "copyright.txt").write_text("2026 Example Ltd\n")
            review = root / "review_information"
            review.mkdir()
            (review / "first_name.txt").write_text("Daniel\n")
            (review / "last_name.txt").write_text("   \n")
            (review / "favourite_colour.txt").write_text("blue")
            found = md.read_global(root)
        self.assertEqual(found.copyright, "2026 Example Ltd")
        self.assertEqual(found.review, {"first_name": "Daniel"})
        joined = " ".join(found.notes)
        self.assertIn("last_name.txt: empty", joined)
        self.assertIn("favourite_colour.txt: not a review information field", joined)

    def test_attributes_map_to_the_api_names_and_demo_account_is_flagged(self):
        attrs, problems, warnings = md.review_attributes({
            "first_name": "Ada", "last_name": "Lovelace", "email_address": "ada@example.com",
            "phone_number": "+34 600 000 000", "demo_user": "reviewer", "demo_password": PASSWORD, "notes": "Open Settings.",
        })
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])
        self.assertEqual(attrs["contactFirstName"], "Ada")
        self.assertEqual(attrs["contactEmail"], "ada@example.com")
        self.assertEqual(attrs["demoAccountName"], "reviewer")
        self.assertIs(attrs["demoAccountRequired"], True)

    def test_problems_block_and_warnings_do_not(self):
        _, problems, _ = md.review_attributes({"demo_user": "only-a-name"})
        self.assertTrue(any("must be given together" in p for p in problems))
        _, problems, _ = md.review_attributes({"email_address": "not-an-email"})
        self.assertTrue(any("does not look like an email" in p for p in problems))
        _, problems, _ = md.review_attributes({"notes": "x" * 4001})
        self.assertTrue(any("4001 characters" in p for p in problems))
        attrs, problems, warnings = md.review_attributes({"phone_number": "600 000 000"})
        self.assertEqual(problems, [])
        self.assertTrue(any("country code" in w for w in warnings))
        self.assertNotIn("demoAccountRequired", attrs)

    def test_plan_with_no_existing_review_detail_sends_everything(self):
        attrs, _, _ = md.review_attributes({"first_name": "Ada", "demo_user": "u", "demo_password": PASSWORD})
        changes, unchanged = md.plan_globals("2026 Me", None, attrs, None)
        self.assertEqual(unchanged, 0)
        self.assertEqual({c.attribute for c in changes},
                         {"copyright", "contactFirstName", "demoAccountName", "demoAccountPassword", "demoAccountRequired"})

    def test_plan_skips_what_already_matches(self):
        attrs, _, _ = md.review_attributes({"first_name": "Ada", "last_name": "Lovelace", "demo_user": "u", "demo_password": "p"})
        current = {"contactFirstName": "Ada", "contactLastName": "Lovelace ", "demoAccountName": "u",
                   "demoAccountPassword": "OLD", "demoAccountRequired": True}
        changes, unchanged = md.plan_globals("2026 Me", "2026 Me", attrs, current)
        self.assertEqual([c.attribute for c in changes], ["demoAccountPassword"])
        self.assertEqual(unchanged, 5)

    def test_describe_never_shows_the_password(self):
        attrs, _, _ = md.review_attributes({"demo_user": "u", "demo_password": PASSWORD})
        changes, _ = md.plan_globals(None, None, attrs, {"demoAccountPassword": "OLD-SECRET"})
        shown = " ".join(md.describe_global(c) for c in changes)
        self.assertNotIn(PASSWORD, shown)
        self.assertNotIn("OLD-SECRET", shown)
        self.assertIn("(set)", shown)


class PushTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "asc-apps.json").write_text('{"app": {"app_id": "1", "metadata_path": "meta"}}')
        (self.root / "meta").mkdir()

    def write(self, relative, text):
        path = self.root / "meta" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")

    def push(self, client, *extra):
        module = types.SimpleNamespace(AppStoreConnectClient=lambda: client, AppStoreConnectError=FakeError)
        argv = ["push_metadata.py", "--apps-file", str(self.root / "asc-apps.json"), "--app", "app",
                "--version", "1.0.0", *extra]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(push_metadata, "load_client_module", return_value=module), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = push_metadata.main()
        return code, out.getvalue(), err.getvalue()

    def test_copyright_alone_updates_the_version_and_touches_no_locale(self):
        self.write("copyright.txt", "2026 Example Ltd")
        self.write("en-US/description.txt", "Should be ignored with --fields copyright")
        client = FakeClient()
        code, out, err = self.push(client, "--fields", "copyright", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual([c for c in client.calls if c[0] != "list_localizations"],
                         [("update_version", {"copyright": "2026 Example Ltd"})])
        self.assertNotIn("list_localizations", [c[0] for c in client.calls])

    def test_review_information_is_created_when_missing_and_never_prints_the_password(self):
        for stem, text in (("first_name", "Ada"), ("last_name", "Lovelace"), ("email_address", "ada@example.com"),
                           ("phone_number", "+34 600 000 000"), ("demo_user", "reviewer"), ("demo_password", PASSWORD)):
            self.write(f"review_information/{stem}.txt", text)
        client = FakeClient(detail=None)
        code, out, err = self.push(client, "--fields", "review_information", "--yes")
        self.assertEqual(code, 0, err)
        created = next(c for c in client.calls if c[0] == "create_review")[1]
        self.assertEqual(created["contactEmail"], "ada@example.com")
        self.assertEqual(created["demoAccountPassword"], PASSWORD)
        self.assertIs(created["demoAccountRequired"], True)
        self.assertNotIn(PASSWORD, out + err)
        self.assertIn("App Review information: created", out)

    def test_review_information_update_sends_only_what_differs(self):
        self.write("review_information/first_name.txt", "Ada")
        self.write("review_information/last_name.txt", "Byron")
        client = FakeClient(detail={"id": "rd9", "attributes": {"contactFirstName": "Ada", "contactLastName": "Lovelace"}})
        code, out, err = self.push(client, "--yes")
        self.assertEqual(code, 0, err)
        update = next(c for c in client.calls if c[0] == "update_review")
        self.assertEqual(update[1:], ("rd9", {"contactLastName": "Byron"}))

    def test_nothing_to_do_when_everything_matches(self):
        self.write("copyright.txt", "2026 Example Ltd")
        client = FakeClient(copyright="2026 Example Ltd")
        code, out, err = self.push(client, "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("No changes", out)
        self.assertEqual([c for c in client.calls if c[0] != "list_localizations"], [])

    def test_a_demo_user_without_a_password_is_refused_before_any_request(self):
        self.write("review_information/demo_user.txt", "reviewer")
        client = FakeClient()
        code, out, err = self.push(client, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("must be given together", err)
        self.assertEqual(client.calls, [])

    def test_dry_run_changes_nothing(self):
        self.write("copyright.txt", "2026 Example Ltd")
        client = FakeClient()
        code, out, err = self.push(client, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("copyright", out)
        self.assertNotIn("update_version", [c[0] for c in client.calls])

    def test_unknown_field_name_lists_the_groups(self):
        self.write("copyright.txt", "x")
        code, _, err = self.push(FakeClient(), "--fields", "bogus")
        self.assertEqual(code, 1)
        self.assertIn("copyright", err)
        self.assertIn("review_information", err)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_password_file_in_a_repo_without_gitignore_is_warned_about(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        secret = repo / "demo_password.txt"
        secret.write_text(PASSWORD)
        self.assertIn("not ignored by git", push_metadata.tracked_secret_warning(secret))
        (repo / ".gitignore").write_text("demo_password.txt\n")
        self.assertIsNone(push_metadata.tracked_secret_warning(secret))
        outside = self.root / "outside.txt"
        outside.write_text("x")
        self.assertIsNone(push_metadata.tracked_secret_warning(outside))


class PullTests(unittest.TestCase):
    def test_pull_writes_copyright_and_review_contact_but_never_the_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "asc-apps.json").write_text('{"app": {"app_id": "1"}}')
            client = types.SimpleNamespace(
                find_version=lambda a, v: {"id": "v1", "attributes": {"copyright": "2026 Example Ltd"}},
                list_localizations=lambda vid: {},
                find_editable_app_info=lambda a: None,
                list_app_infos=lambda a: [],
                get_review_detail=lambda vid: {"id": "rd", "attributes": {
                    "contactFirstName": "Ada", "contactEmail": "ada@example.com",
                    "demoAccountName": "reviewer", "demoAccountPassword": PASSWORD, "notes": ""}},
            )
            module = types.SimpleNamespace(AppStoreConnectClient=lambda: client, AppStoreConnectError=FakeError)
            out = io.StringIO()
            argv = ["pull_metadata.py", "--apps-file", str(root / "asc-apps.json"), "--app", "app",
                    "--version", "1.0.0", "--dir", str(root / "out")]
            with mock.patch.object(pull_metadata, "load_client_module", return_value=module), \
                    mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
                code = pull_metadata.main()
            self.assertEqual(code, 0)
            written = {str(p.relative_to(root / "out")) for p in (root / "out").rglob("*.txt")}
            self.assertEqual(written, {"copyright.txt", "review_information/first_name.txt",
                                       "review_information/email_address.txt", "review_information/demo_user.txt"})
            self.assertNotIn(PASSWORD, out.getvalue())
            for p in (root / "out").rglob("*.txt"):
                self.assertNotIn(PASSWORD, p.read_text())


if __name__ == "__main__":
    unittest.main()
