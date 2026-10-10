#!/usr/bin/env python3
"""Collect what changed since the last release, as raw material for drafting
release notes. Read-only: it only runs `git log` / `git describe` and reads
files; it never contacts App Store Connect and needs no credentials.

The range is "last tag reachable from HEAD" .. HEAD. Each commit is tagged
by the kinds of files it touched, so documentation-only or build-setting-only
commits are easy to tell apart from ones that can change what a user sees.
Obvious version-bump commits are set aside and counted.

Usage:
    python collect_changes.py --app my-app
    python collect_changes.py --repo ~/code/my-app
    python collect_changes.py --app my-app --since release.1.9.0.20 --bodies
    python collect_changes.py --repo . --tag-pattern 'release.*' --path Sources/
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from asc_common import ConfigError, add_app_arguments, display_path, resolve_app
from release_notes import parse as parse_release_notes

# "Upgrade version", "bump version to 1.2", "Update build number"...
NOISE_RE = re.compile(
    r"^(up(grade|date)|bump)( the)?( app)?[ _-](version|build)( number)?( to .*)?\.?$", re.IGNORECASE
)
CATEGORY_ORDER = ("code", "l10n", "assets", "project", "tests", "docs", "other")
MEDIA_EXTENSIONS = (".png", ".jpg", ".jpeg", ".pdf", ".svg", ".heic", ".mp3", ".wav", ".m4a", ".caf")
IGNORED_PROJECT_DIRS = {"Pods", "DerivedData", ".build", "Carthage", "node_modules"}
SAMPLE_CHARS = 1200


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


# -- Classification ------------------------------------------------------------


def categorize(path: str) -> str:
    lower = path.lower()
    parts = lower.split("/")
    name = parts[-1]
    if lower.endswith((".xcstrings", ".strings", ".stringsdict")):
        return "l10n"
    if ".xcassets/" in lower or lower.endswith(MEDIA_EXTENSIONS):
        return "assets"
    if lower.endswith((".pbxproj", ".xcconfig", ".plist", ".entitlements", ".xcscheme", ".xcworkspacedata")) or (
        name == "package.resolved"
    ):
        return "project"
    if any(part.endswith(("tests", "test")) for part in parts[:-1]) or re.search(r"tests?\.swift$", name):
        return "tests"
    if lower.endswith((".md", ".txt", ".rst")) or "docs" in parts[:-1]:
        return "docs"
    if lower.endswith((".swift", ".m", ".mm", ".h", ".c", ".cc", ".cpp", ".metal", ".storyboard", ".xib",
                       ".intentdefinition", ".kt", ".java")):
        return "code"
    return "other"


def is_noise(subject: str) -> bool:
    return bool(NOISE_RE.match(subject.strip()))


# -- Reading the repository -----------------------------------------------------


@dataclass
class Commit:
    sha: str
    date: str
    subject: str
    body: str
    files: list = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        found = {categorize(f) for f in self.files}
        return [c for c in CATEGORY_ORDER if c in found]


def parse_log(raw: str) -> list[Commit]:
    """Parses `git log --format=@@@%h\\x1f%ad\\x1f%s\\x1f%b\\x1e --name-only`."""
    commits = []
    for chunk in raw.split("@@@")[1:]:
        header, _, files_part = chunk.partition("\x1e")
        fields = header.split("\x1f")
        if len(fields) < 3:
            continue
        sha, date, subject = fields[0], fields[1], fields[2]
        body = fields[3].strip() if len(fields) > 3 else ""
        files = [line for line in files_part.splitlines() if line.strip()]
        commits.append(Commit(sha, date, subject, body, files))
    return commits


def latest_tag(repo: Path, pattern: str | None) -> str:
    args = ["describe", "--tags", "--abbrev=0"]
    if pattern:
        args += ["--match", pattern]
    try:
        return git(repo, *args, "HEAD").strip()
    except GitError as exc:
        raise GitError(f"no tag found from HEAD ({exc}); pass --since <tag or commit>") from exc


def project_versions(repo: Path) -> tuple[list[str], list[str]]:
    """Distinct MARKETING_VERSION and CURRENT_PROJECT_VERSION (build number)
    values in tracked Xcode projects. Uses `git ls-files` so it stays fast on
    large trees and skips untracked output.
    """
    marketing, builds = set(), set()
    for relative in git(repo, "ls-files", "*project.pbxproj").splitlines():
        if IGNORED_PROJECT_DIRS & set(Path(relative).parts):
            continue
        try:
            text = (repo / relative).read_text(errors="ignore")
        except OSError:
            continue
        marketing.update(m.strip().strip('"') for m in re.findall(r"MARKETING_VERSION = ([^;]+);", text))
        builds.update(m.strip().strip('"') for m in re.findall(r"CURRENT_PROJECT_VERSION = ([^;]+);", text))
    return sorted(marketing), sorted(builds)


# -- Rendering ---------------------------------------------------------------


def render(
    repo: Path,
    since: str,
    commits: list[Commit],
    noise: list[Commit],
    versions: tuple[list[str], list[str]],
    notes_path: Path | None,
    bodies: bool,
    max_commits: int,
) -> str:
    out = []
    out.append(f"Repository: {repo}")
    out.append(f"Range:      {since}..HEAD")
    total = len(commits) + len(noise)
    span = ""
    dates = sorted(c.date for c in commits + noise)
    if dates:
        span = f", {dates[0]} to {dates[-1]}"
    out.append(f"Commits:    {total}{span} ({len(noise)} version-bump commit(s) set aside)")
    marketing, builds = versions
    out.append(f"Project MARKETING_VERSION:        {', '.join(marketing) or 'none found'}")
    out.append(f"Project CURRENT_PROJECT_VERSION:  {', '.join(builds) or 'none found'}")

    if notes_path is not None:
        out.append("")
        out.append(f"Existing notes file: {display_path(notes_path)}")
        if notes_path.exists():
            heading = next((l for l in notes_path.read_text().splitlines() if l.startswith("# ")), None)
            texts = parse_release_notes(notes_path)
            if heading:
                out.append(f"  heading: {heading}")
            out.append(f"  locales ({len(texts)}): {', '.join(texts)}")
            sample_locale = "en-US" if "en-US" in texts else next(iter(texts), None)
            if sample_locale:
                sample = texts[sample_locale][:SAMPLE_CHARS]
                out.append(f"  previous {sample_locale} text, for tone and length:")
                out.extend(f"    | {line}" for line in sample.splitlines())
        else:
            out.append("  (file does not exist yet)")

    out.append("")
    if not commits:
        out.append("No user-relevant commits since that point: nothing to describe.")
        return "\n".join(out)

    shown = commits[-max_commits:] if len(commits) > max_commits else commits
    out.append("Commits, oldest first. [kinds of files touched]")
    if len(shown) < len(commits):
        out.append(f"  (showing the newest {len(shown)} of {len(commits)})")
    for commit in shown:
        kinds = "+".join(commit.categories) or "empty"
        out.append(f"  {commit.sha}  [{kinds}]  {commit.subject}")
        if bodies and commit.body:
            out.extend(f"      {line}" for line in commit.body.splitlines())

    areas = Counter("/".join(f.split("/")[:2]) for c in commits for f in c.files)
    if areas:
        out.append("")
        out.append("Most-touched areas: " + ", ".join(f"{a} ({n})" for a, n in areas.most_common(6)))
    out.append("")
    out.append(
        "Reading guide: [docs], [project] and [tests] on their own rarely change what a user sees; "
        "[code], [l10n] and [assets] usually do. Verify with `git show <sha>` when unsure."
    )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_app_arguments(parser)
    parser.add_argument("--repo", help="Git repository (default: the one holding the app's release notes, else the cwd)")
    parser.add_argument("--since", help="Start of the range: a tag or commit (default: the latest tag reachable from HEAD)")
    parser.add_argument("--tag-pattern", help="Glob limiting which tags count as releases, e.g. 'release.*'")
    parser.add_argument("--path", action="append", default=[], help="Only count changes under this path (repeatable)")
    parser.add_argument("--bodies", action="store_true", help="Include commit message bodies")
    parser.add_argument("--keep-noise", action="store_true", help="Do not set version-bump commits aside")
    parser.add_argument("--max-commits", type=int, default=200, help="Cap on commits listed (default: 200)")
    args = parser.parse_args()

    notes_path = None
    if args.app or args.apps_file:
        try:
            app = resolve_app(args)
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            return 1
        notes_path = app.release_notes_path

    start = Path(args.repo).expanduser() if args.repo else (notes_path.parent if notes_path else Path.cwd())
    try:
        repo = Path(git(start, "rev-parse", "--show-toplevel").strip())
        since = args.since or latest_tag(repo, args.tag_pattern)
        log_args = [
            "log", f"{since}..HEAD", "--reverse", "--no-merges", "--date=short",
            "--format=@@@%h%x1f%ad%x1f%s%x1f%b%x1e", "--name-only",
        ]
        if args.path:
            log_args += ["--", *args.path]
        commits = parse_log(git(repo, *log_args))
        versions = project_versions(repo)
    except GitError as exc:
        print(f"git error: {exc}", file=sys.stderr)
        return 1

    noise = [] if args.keep_noise else [c for c in commits if is_noise(c.subject)]
    kept = [c for c in commits if c not in noise]
    print(render(repo, since, kept, noise, versions, notes_path, args.bodies, args.max_commits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
