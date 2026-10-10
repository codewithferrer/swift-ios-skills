"""Offline tests for the release notes checks and the change collector.

    python3 -m unittest discover -s tests -v

Needs no credentials and no network. The collector tests build a throwaway
git repository and are skipped when git is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect_changes as cc  # noqa: E402
import release_notes as rn  # noqa: E402

HAVE_GIT = shutil.which("git") is not None


class EmojiAndLimitTests(unittest.TestCase):
    def test_flags_emoji_but_not_ordinary_symbols_or_scripts(self):
        self.assertEqual(rn.find_emoji("Faster \U0001F680 and ✨"), ["✨", "\U0001F680"])
        plain = "Arrows → ←, check ✓, café, 日本語, مرحبا, (c) ™"
        self.assertEqual(rn.find_emoji(plain), [])

    def test_variation_selector_alone_is_reported(self):
        problems = rn.check({"en-US": "I ❤️ it"})
        self.assertEqual(len(problems), 1)
        self.assertIn("emoji variation selector", problems[0])

    def test_check_reports_each_locale_and_can_allow_emoji(self):
        texts = {"en-US": "fine", "es-ES": "x" * 4001, "ja": "hi \U0001F389"}
        problems = rn.check(texts)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any(p.startswith("es-ES: 4001 characters") for p in problems))
        self.assertTrue(any(p.startswith("ja: contains emoji") for p in problems))
        self.assertEqual(len(rn.check(texts, allow_emoji=True)), 1)

    def test_exactly_at_the_limit_is_fine(self):
        self.assertEqual(rn.check({"en-US": "x" * 4000}), [])


class ClassificationTests(unittest.TestCase):
    def test_categories(self):
        cases = {
            "App/Views/Home.swift": "code",
            "App/Localizable.xcstrings": "l10n",
            "App/Assets.xcassets/Icon.appiconset/Contents.json": "assets",
            "art/hero.png": "assets",
            "App.xcodeproj/project.pbxproj": "project",
            "App/Info.plist": "project",
            "AppTests/HomeTests.swift": "tests",
            "README.md": "docs",
            "docs/design.pdf": "assets",
            "docs/notes.md": "docs",
            "Makefile": "other",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(cc.categorize(path), expected)

    def test_noise_detection(self):
        for subject in ("Upgrade version", "upgrade version", "Bump version to 1.9.3", "Update build number", "bump build"):
            with self.subTest(subject=subject):
                self.assertTrue(cc.is_noise(subject))
        for subject in ("Add dark mode", "Fix crash when upgrading version history", "Bump up the volume"):
            with self.subTest(subject=subject):
                self.assertFalse(cc.is_noise(subject))

    def test_parse_log(self):
        raw = (
            "@@@abc1234\x1f2026-09-01\x1fAdd dark mode\x1fBody line\x1e\n\nApp/Theme.swift\nApp/Localizable.xcstrings\n"
            "@@@def5678\x1f2026-09-02\x1fUpdate README\x1f\x1e\n\nREADME.md\n"
        )
        commits = cc.parse_log(raw)
        self.assertEqual([c.sha for c in commits], ["abc1234", "def5678"])
        self.assertEqual(commits[0].body, "Body line")
        self.assertEqual(commits[0].categories, ["code", "l10n"])
        self.assertEqual(commits[1].categories, ["docs"])


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class CollectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.git("init", "-q")

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            check=True, capture_output=True,
        )

    def commit(self, filename: str, subject: str, content: str = "x"):
        path = self.repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + subject)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)

    def run_collector(self, *extra):
        script = Path(cc.__file__)
        result = subprocess.run(
            [sys.executable, str(script), "--repo", str(self.repo), *extra],
            capture_output=True, text=True,
        )
        return result

    def test_range_starts_at_latest_tag_and_sets_noise_aside(self):
        self.commit("Sources/Old.swift", "Old work before the release")
        self.git("tag", "release.1.0.0.1")
        self.commit("Sources/Theme.swift", "Add dark mode")
        self.commit("App.xcodeproj/project.pbxproj", "Upgrade version", 'MARKETING_VERSION = 1.1.0;')
        self.commit("README.md", "Update README")

        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("release.1.0.0.1..HEAD", out)
        self.assertIn("Add dark mode", out)
        self.assertIn("[code]", out)
        self.assertIn("[docs]", out)
        self.assertNotIn("Old work before the release", out)
        self.assertNotIn("Upgrade version", out)
        self.assertIn("1 version-bump commit(s) set aside", out)
        self.assertIn("1.1.0", out)

    def test_keep_noise_and_explicit_since(self):
        self.commit("a.swift", "First")
        self.commit("b.swift", "Upgrade version")
        result = self.run_collector("--since", "HEAD~1", "--keep-noise")
        self.assertIn("Upgrade version", result.stdout)

    def test_no_tag_is_a_clear_error(self):
        self.commit("a.swift", "First")
        result = self.run_collector()
        self.assertEqual(result.returncode, 1)
        self.assertIn("--since", result.stderr)

    def test_nothing_new_since_tag(self):
        self.commit("a.swift", "First")
        self.git("tag", "v1")
        result = self.run_collector()
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to describe", result.stdout)


if __name__ == "__main__":
    unittest.main()
