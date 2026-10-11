"""Offline tests for the release train: the project parser, the changelog
rollover and the CLI against throwaway git repositories (a bare "origin" and a
clone). No Xcode, no network.

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import changelog as cl  # noqa: E402
import pbxproj  # noqa: E402
import release_train as rt  # noqa: E402

HAVE_GIT = shutil.which("git") is not None


def make_pbxproj(app_version="1.9.2", app_build="22", watch_build="22", tests_build="1", project_build=None,
                 extra_app_settings="") -> str:
    """A project with an iOS app, a watch app and a test target."""
    def config(cid, name, settings):
        body = "".join(f"\t\t\t\t{line}\n" for line in settings)
        return (f"\t\t{cid} /* {name} */ = {{\n\t\t\tisa = XCBuildConfiguration;\n\t\t\tbuildSettings = {{\n"
                f"{body}\t\t\t}};\n\t\t\tname = {name};\n\t\t}};\n")

    def clist(lid, label, ids):
        refs = "".join(f"\t\t\t\t{i} /* {n} */,\n" for i, n in ids)
        return (f"\t\t{lid} /* Build configuration list for {label} */ = {{\n\t\t\tisa = XCConfigurationList;\n"
                f"\t\t\tbuildConfigurations = (\n{refs}\t\t\t);\n\t\t\tdefaultConfigurationName = Release;\n\t\t}};\n")

    def target(tid, name, ptype, lid):
        return (f"\t\t{tid} /* {name} */ = {{\n\t\t\tisa = PBXNativeTarget;\n"
                f"\t\t\tbuildConfigurationList = {lid} /* Build configuration list for PBXNativeTarget \"{name}\" */;\n"
                f"\t\t\tname = \"{name}\";\n\t\t\tproductType = \"{ptype}\";\n\t\t}};\n")

    proj_settings = ['IPHONEOS_DEPLOYMENT_TARGET = 18.0;']
    if project_build:
        proj_settings.append(f"CURRENT_PROJECT_VERSION = {project_build};")
    app = ['CODE_SIGN_STYLE = Automatic;', *( [f"CURRENT_PROJECT_VERSION = {app_build};"] if app_build else []),
           f"MARKETING_VERSION = {app_version};", 'PRODUCT_NAME = "$(TARGET_NAME)";', 'SDKROOT = iphoneos;']
    if extra_app_settings:
        app.append(extra_app_settings)
    watch = [f"CURRENT_PROJECT_VERSION = {watch_build};", "MARKETING_VERSION = 1.0;", "SDKROOT = watchos;",
             "WATCHOS_DEPLOYMENT_TARGET = 11.0;"]
    tests = [f"CURRENT_PROJECT_VERSION = {tests_build};", "MARKETING_VERSION = 1.0;"]
    A = lambda n: f"{n:024X}"  # noqa: E731

    parts = ["// !$*UTF8*$!\n{\n\tobjects = {\n\n/* Begin PBXNativeTarget section */\n",
             target(A(1), "App", pbxproj.IOS_APP, A(11)),
             target(A(2), "App Watch App", pbxproj.IOS_APP, A(12)),
             target(A(3), "AppTests", "com.apple.product-type.bundle.unit-test", A(13)),
             "/* End PBXNativeTarget section */\n\n/* Begin PBXProject section */\n",
             f"\t\t{A(9)} /* Project object */ = {{\n\t\t\tisa = PBXProject;\n"
             f"\t\t\tbuildConfigurationList = {A(10)} /* Build configuration list for PBXProject \"App\" */;\n\t\t}};\n",
             "/* End PBXProject section */\n\n/* Begin XCBuildConfiguration section */\n"]
    for cid, name, settings in ((A(100), "Debug", proj_settings), (A(101), "Release", proj_settings),
                                (A(110), "Debug", app), (A(111), "Release", app),
                                (A(120), "Debug", watch), (A(121), "Release", watch),
                                (A(130), "Debug", tests), (A(131), "Release", tests)):
        parts.append(config(cid, name, settings))
    parts.append("/* End XCBuildConfiguration section */\n\n/* Begin XCConfigurationList section */\n")
    for lid, label, ids in ((A(10), 'PBXProject "App"', ((A(100), "Debug"), (A(101), "Release"))),
                            (A(11), 'PBXNativeTarget "App"', ((A(110), "Debug"), (A(111), "Release"))),
                            (A(12), 'PBXNativeTarget "App Watch App"', ((A(120), "Debug"), (A(121), "Release"))),
                            (A(13), 'PBXNativeTarget "AppTests"', ((A(130), "Debug"), (A(131), "Release")))):
        parts.append(clist(lid, label, ids))
    parts.append("/* End XCConfigurationList section */\n\t};\n\trootObject = " + A(9) + ";\n}\n")
    return "".join(parts)


class ProjectParserTests(unittest.TestCase):
    def load(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "App.xcodeproj"
            path.mkdir()
            (path / "project.pbxproj").write_text(make_pbxproj(**kw))
            return pbxproj.load(path)

    def test_the_app_target_is_the_ios_app_not_the_watch_app_or_tests(self):
        project = self.load()
        self.assertEqual([t.name for t in project.app_targets()], ["App"])

    def test_version_and_build_are_those_of_the_app_not_the_first_match(self):
        project = self.load()
        app = project.app_targets()[0]
        self.assertEqual(project.resolve(app, "Release", pbxproj.VERSION_KEY).value, "1.9.2")
        self.assertEqual(project.resolve(app, "Release", pbxproj.BUILD_KEY).value, "22")

    def test_a_setting_inherited_from_the_project_level_is_resolved(self):
        project = self.load(app_build=None, project_build="7")
        app = project.app_targets()[0]
        setting = project.resolve(app, "Release", pbxproj.BUILD_KEY)
        self.assertEqual(setting.value, "7")
        self.assertEqual(project.owner_of(next(c for c in project.configs if project.configs[c].settings.get(
            pbxproj.BUILD_KEY) is setting)), "(project)")

    def test_bump_edits_the_app_and_watch_but_not_the_tests(self):
        project = self.load()
        edits = project.plan_change(pbxproj.BUILD_KEY, "22", "23")
        self.assertEqual(sorted({e.owner for e in edits}), ["App", "App Watch App"])
        text = project.apply(edits)
        self.assertEqual(text.count("CURRENT_PROJECT_VERSION = 23;"), 4)
        self.assertEqual(text.count("CURRENT_PROJECT_VERSION = 1;"), 2)

    def test_everything_else_is_left_byte_for_byte(self):
        project = self.load()
        original = "".join(project.lines)
        updated = project.apply(project.plan_change(pbxproj.BUILD_KEY, "22", "23"))
        diff = [(a, b) for a, b in zip(original.splitlines(), updated.splitlines()) if a != b]
        self.assertEqual(len(diff), 4)
        for a, b in diff:
            self.assertEqual(a.replace("= 22;", "= 23;"), b)
        self.assertEqual(len(original.splitlines()), len(updated.splitlines()))

    def test_quoted_values_keep_their_quotes(self):
        text = make_pbxproj().replace("MARKETING_VERSION = 1.9.2;", 'MARKETING_VERSION = "1.9.2";')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "App.xcodeproj"
            path.mkdir()
            (path / "project.pbxproj").write_text(text)
            project = pbxproj.load(path)
        updated = project.apply(project.plan_change(pbxproj.VERSION_KEY, "1.9.2", "1.10.0"))
        self.assertIn('MARKETING_VERSION = "1.10.0";', updated)

    def test_two_app_candidates_are_reported_not_guessed(self):
        text = make_pbxproj().replace("SDKROOT = watchos;", "SDKROOT = iphoneos;").replace(
            "WATCHOS_DEPLOYMENT_TARGET = 11.0;", "IPHONEOS_DEPLOYMENT_TARGET = 18.0;")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "App.xcodeproj"
            path.mkdir()
            (path / "project.pbxproj").write_text(text)
            self.assertEqual(len(pbxproj.load(path).app_targets()), 2)


class ChangelogTests(unittest.TestCase):
    FLAT = "# Changelog\n\n## [Unreleased]\n- Added a thing\n- Fixed a bug\n\n## [1.0.0] - 2026-01-01\n- First\n"
    GROUPED = "# Changelog\n\n## [Unreleased]\n\n### Added\n- One\n\n### Fixed\n- Two\n\n## [0.9.0] - 2025-12-01\n- Old\n"

    def test_counts_entries_in_flat_and_grouped_styles(self):
        self.assertEqual(cl.count_entries(self.FLAT), 2)
        self.assertEqual(cl.count_entries(self.GROUPED), 2)
        self.assertEqual(cl.count_entries("# Changelog\n"), 0)

    def test_rollover_keeps_everything_and_adds_an_empty_unreleased(self):
        out = cl.rollover(self.FLAT, "1.1.0", "2026-10-11")
        self.assertIn("## [Unreleased]\n\n## [1.1.0] - 2026-10-11\n- Added a thing\n- Fixed a bug", out)
        self.assertIn("## [1.0.0] - 2026-01-01\n- First", out)
        self.assertEqual(cl.count_entries(out), 0)
        self.assertEqual(out.count("[Unreleased]"), 1)

    def test_grouped_categories_move_with_the_version(self):
        out = cl.rollover(self.GROUPED, "1.0.0", "2026-10-11")
        self.assertIn("## [1.0.0] - 2026-10-11\n\n### Added\n- One", out)

    def test_refuses_empty_duplicate_or_missing(self):
        with self.assertRaises(cl.ChangelogError):
            cl.rollover("## [Unreleased]\n\n## [1.0.0] - x\n- a\n", "1.1.0", "d")
        with self.assertRaises(cl.ChangelogError):
            cl.rollover(self.FLAT, "1.0.0", "d")
        with self.assertRaises(cl.ChangelogError):
            cl.rollover("# Changelog\n- a\n", "1.0.0", "d")
        cl.rollover("## [Unreleased]\n\n## [1.0.0] - x\n- a\n", "1.1.0", "d", allow_empty=True)


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class CliTests(unittest.TestCase):
    def setUp(self):
        # The tool under test runs git itself, so the identity has to be in the
        # environment: a CI runner has no global git config, and a developer
        # machine's (signing, hooks) should not leak into the test either.
        env = mock.patch.dict(os.environ, {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        })
        env.start()
        self.addCleanup(env.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.origin = self.root / "origin.git"
        self.work = self.root / "work"
        self.git(self.root, "init", "-q", "--bare", str(self.origin))
        self.git(self.root, "clone", "-q", str(self.origin), str(self.work))
        self.git(self.work, "checkout", "-q", "-b", "main")
        proj = self.work / "App.xcodeproj"
        proj.mkdir()
        (proj / "project.pbxproj").write_text(make_pbxproj())
        (self.work / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n- Something\n")
        self.git(self.work, "add", "-A")
        self.commit("Initial")
        self.git(self.work, "push", "-q", "-u", "origin", "main")

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            check=True, capture_output=True, text=True)

    def commit(self, message):
        self.git(self.work, "commit", "-q", "-m", message)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        command, rest = argv[0], list(argv[1:])
        full = ["release_train.py", command, "--no-xcodebuild", "--project", str(self.work / "App.xcodeproj"), *rest]
        with mock.patch.object(sys, "argv", full), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rt.main()
        return code, out.getvalue(), err.getvalue()

    def test_info_reports_the_app_values_and_where_the_build_lives(self):
        code, out, err = self.run_cli("info")
        self.assertEqual(code, 0, err)
        self.assertIn("Version:      1.9.2", out)
        self.assertIn("Build:        22", out)
        self.assertIn("App Watch App", out)
        self.assertIn("release.1.9.2.22", out)
        self.assertIn("1 Unreleased entry", out)

    def test_check_passes_on_a_clean_pushed_tree(self):
        code, out, err = self.run_cli("check")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Ready to tag", out)

    def test_check_fails_on_uncommitted_changes_wrong_branch_and_unpushed_head(self):
        (self.work / "CHANGELOG.md").write_text("changed\n")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] Working tree clean", out)
        self.git(self.work, "checkout", "-q", "-b", "feature")
        code, out, _ = self.run_cli("check", "--allow-untracked")
        self.assertIn("[FAIL] On main", out)

        self.git(self.work, "checkout", "-q", "main")
        self.git(self.work, "add", "-A")
        self.commit("Unpushed work")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] HEAD is pushed", out)

    def test_tag_previews_by_default_and_pushes_only_that_tag_with_the_flag(self):
        code, out, err = self.run_cli("tag")
        self.assertEqual(code, 0, err)
        self.assertIn("Preview only", out)
        self.assertEqual(self.git(self.work, "tag", "--list").stdout.strip(), "")

        self.git(self.work, "tag", "unrelated-local-tag")
        code, out, err = self.run_cli("tag", "--push", "--yes")
        self.assertEqual(code, 0, err)
        remote_tags = self.git(self.work, "ls-remote", "--tags", "origin").stdout
        self.assertIn("refs/tags/release.1.9.2.22", remote_tags)
        self.assertNotIn("unrelated-local-tag", remote_tags)

        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("Tag is new locally", out)

    def test_pushing_needs_the_tag_typed_unless_yes_is_given(self):
        for typed, label in (("y", "a stray yes"), ("release.9.9.9.9", "another tag")):
            with self.subTest(label), mock.patch("builtins.input", return_value=typed):
                code, out, _ = self.run_cli("tag", "--push")
            self.assertEqual(code, 1)
            self.assertIn("did not match", out)
        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = self.run_cli("tag", "--push")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(self.git(self.work, "tag", "--list").stdout.strip(), "")
        self.assertNotIn("release.1.9.2.22", self.git(self.work, "ls-remote", "--tags", "origin").stdout)

        with mock.patch("builtins.input", return_value="release.1.9.2.22"):
            code, out, err = self.run_cli("tag", "--push")
        self.assertEqual(code, 0, err)
        self.assertIn("refs/tags/release.1.9.2.22", self.git(self.work, "ls-remote", "--tags", "origin").stdout)

    def test_a_lower_build_than_one_already_tagged_is_refused(self):
        self.git(self.work, "tag", "-a", "release.1.9.2.30", "-m", "x")
        code, out, _ = self.run_cli("check", "--no-fetch")
        self.assertEqual(code, 1)
        self.assertIn("already tagged with build(s) [30]", out)

    def test_bump_previews_then_writes_only_the_shipping_targets(self):
        code, out, _ = self.run_cli("bump")
        self.assertEqual(code, 0)
        self.assertIn("22 -> 23", out)
        self.assertIn("Preview only", out)
        self.assertIn("AppTests (1)", out)
        before = (self.work / "App.xcodeproj" / "project.pbxproj").read_text()
        self.assertIn("CURRENT_PROJECT_VERSION = 22;", before)

        code, out, _ = self.run_cli("bump", "--version", "1.10.0", "--write")
        self.assertEqual(code, 0, out)
        after = (self.work / "App.xcodeproj" / "project.pbxproj").read_text()
        self.assertEqual(after.count("CURRENT_PROJECT_VERSION = 23;"), 4)
        self.assertIn("MARKETING_VERSION = 1.10.0;", after)
        self.assertEqual(after.count("MARKETING_VERSION = 1.0;"), 4)  # watch + tests untouched

    def test_changelog_preview_and_write(self):
        code, out, _ = self.run_cli("changelog", "--date", "2026-10-11")
        self.assertEqual(code, 0)
        self.assertIn("becomes [1.9.2] - 2026-10-11", out)
        self.assertNotIn("1.9.2", (self.work / "CHANGELOG.md").read_text())
        self.run_cli("changelog", "--date", "2026-10-11", "--write")
        text = (self.work / "CHANGELOG.md").read_text()
        self.assertIn("## [Unreleased]\n\n## [1.9.2] - 2026-10-11\n- Something", text)

    def test_build_and_test_commands_gate_the_checks(self):
        code, out, _ = self.run_cli("check", "--build-cmd", "true", "--test-cmd", "exit 3")
        self.assertEqual(code, 1)
        self.assertIn("[ok  ] Build command", out)
        self.assertIn("[FAIL] Test command", out)


if __name__ == "__main__":
    unittest.main()
