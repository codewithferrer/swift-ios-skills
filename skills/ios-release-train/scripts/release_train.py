#!/usr/bin/env python3
"""The repository side of shipping an iOS version: find the app's version and
build number reliably, check the repo is ready, bump the build, close the
changelog, and create and push the release tag that starts the CI build
(Xcode Cloud, for the convention this was written for).

    info       What the project, the repo and the changelog look like now.
    check      The guards a release tag needs; exit 1 when any fails.
    bump       Raise the build number (and optionally set the version).
    changelog  Close [Unreleased] as the given version.
    tag        Run the checks, then create the tag and push only it.

It never touches App Store Connect and needs no credentials; the next step
after a tag is pushed is whatever sends that build to TestFlight and the
store. Anything that changes a file or pushes is preview-only unless you pass
--write (bump, changelog) or --push (tag).

Usage:
    python release_train.py info
    python release_train.py check --build-cmd ".claude/skills/build.sh release" --test-cmd ".claude/skills/test.sh"
    python release_train.py bump                 # preview
    python release_train.py bump --write
    python release_train.py changelog --version 1.9.3 --write
    python release_train.py tag                  # preview, runs the checks
    python release_train.py tag --push
"""

from __future__ import annotations

import argparse
import datetime
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import changelog as changelog_mod
import pbxproj

DEFAULT_TAG_FORMAT = "release.{version}.{build}"
VERSION_RE = re.compile(r"^\d+(\.\d+){1,2}$")


class ReleaseError(RuntimeError):
    pass


# -- git ------------------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def repo_root(start: Path) -> Path:
    result = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"{start} is not inside a git repository")
    return Path(result.stdout.strip())


# -- project -----------------------------------------------------------------------


def find_project(explicit: str | None, cwd: Path) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not (path / "project.pbxproj").is_file():
            raise ReleaseError(f"{path} is not an .xcodeproj (no project.pbxproj inside)")
        return path.resolve()
    found = sorted(cwd.glob("*.xcodeproj"))
    if len(found) == 1:
        return found[0].resolve()
    if not found:
        raise ReleaseError(f"no .xcodeproj in {cwd}; run from the project folder or pass --project")
    raise ReleaseError("several .xcodeproj here (" + ", ".join(p.name for p in found) + "); pass --project")


@dataclass
class Detection:
    project: pbxproj.Project
    xcodeproj: Path
    target: pbxproj.Target
    version: str
    build: str
    owners_of_build: list = field(default_factory=list)
    xcodebuild: dict | None = None
    warnings: list = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return self.xcodebuild is None or (
            self.xcodebuild.get(pbxproj.VERSION_KEY) == self.version
            and self.xcodebuild.get(pbxproj.BUILD_KEY) == self.build
        )


def xcodebuild_settings(xcodeproj: Path, target: str, configuration: str) -> dict | None:
    if not shutil.which("xcodebuild"):
        return None
    try:
        result = subprocess.run(
            ["xcodebuild", "-project", str(xcodeproj), "-target", target, "-configuration", configuration,
             "-showBuildSettings"],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    settings = {}
    for line in result.stdout.splitlines():
        m = re.match(r"^\s+([A-Z0-9_]+) = (.*)$", line)
        if m:
            settings[m.group(1)] = m.group(2).strip()
    return settings or None


def detect(xcodeproj: Path, target_name: str | None, configuration: str, use_xcodebuild: bool = True) -> Detection:
    project = pbxproj.load(xcodeproj)
    if target_name:
        matches = [t for t in project.targets if t.name == target_name]
        if not matches:
            raise ReleaseError(f"no target named {target_name!r}; targets: {', '.join(t.name for t in project.targets)}")
        target = matches[0]
    else:
        candidates = project.app_targets(configuration)
        if not candidates:
            raise ReleaseError("no iOS app target found; pass --target")
        if len(candidates) > 1:
            raise ReleaseError("several app targets (" + ", ".join(t.name for t in candidates) + "); pass --target")
        target = candidates[0]

    version = project.resolve(target, configuration, pbxproj.VERSION_KEY)
    build = project.resolve(target, configuration, pbxproj.BUILD_KEY)
    if version is None or build is None:
        raise ReleaseError(f"{target.name} has no {pbxproj.VERSION_KEY} / {pbxproj.BUILD_KEY} in the project file "
                           "(they may come from an .xcconfig; read them with xcodebuild -showBuildSettings)")
    found = Detection(project, xcodeproj, target, version.value, build.value)
    found.owners_of_build = sorted({e.owner for e in project.sharing(pbxproj.BUILD_KEY, build.value)})
    config = project.target_config(target, configuration)
    if config and config.has_xcconfig:
        found.warnings.append("the target uses an .xcconfig file, which may override these values")
    if use_xcodebuild:
        found.xcodebuild = xcodebuild_settings(xcodeproj, target.name, configuration)
        if found.xcodebuild is None:
            found.warnings.append("xcodebuild could not be used to confirm the values; relying on the project file")
        elif not found.consistent:
            found.warnings.append(
                f"the project file says {found.version} ({found.build}) but xcodebuild says "
                f"{found.xcodebuild.get(pbxproj.VERSION_KEY)} ({found.xcodebuild.get(pbxproj.BUILD_KEY)}); "
                "an .xcconfig or a build script is probably involved"
            )
    return found


def tag_for(fmt: str, version: str, build: str) -> str:
    return fmt.format(version=version, build=build)


def tag_glob(fmt: str) -> str:
    return fmt.replace("{version}", "*").replace("{build}", "*")


def existing_builds(repo: Path, fmt: str, version: str) -> list[int]:
    """Build numbers already tagged for this version."""
    pattern = re.escape(fmt).replace(re.escape("{version}"), re.escape(version)).replace(
        re.escape("{build}"), r"(\d+)")
    out = git(repo, "tag", "--list", tag_glob(fmt), check=False).stdout.split()
    builds = []
    for name in out:
        m = re.fullmatch(pattern, name)
        if m:
            builds.append(int(m.group(1)))
    return sorted(builds)


# -- checks ---------------------------------------------------------------------------


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""
    warning: bool = False  # a failing warning does not fail the run


def run_checks(det: Detection, repo: Path, args) -> list[Check]:
    checks: list[Check] = []
    add = lambda *a, **k: checks.append(Check(*a, **k))  # noqa: E731
    tag = tag_for(args.tag_format, det.version, det.build)

    add("Version format", bool(VERSION_RE.match(det.version)), f"{det.version}" if VERSION_RE.match(det.version)
        else f"{det.version!r} is not X.Y or X.Y.Z")
    add("Build number", det.build.isdigit(), det.build if det.build.isdigit() else f"{det.build!r} is not a number")
    add("Settings agree with xcodebuild", det.consistent,
        "" if det.consistent else "the project file and xcodebuild disagree; fix the source before tagging")

    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not args.any_branch:
        add(f"On {args.branch}", branch == args.branch, f"on {branch!r}" if branch != args.branch else branch)

    status = [l for l in git(repo, "status", "--porcelain").stdout.splitlines()
              if not (args.allow_untracked and l.startswith("??"))]
    add("Working tree clean", not status, "" if not status else f"{len(status)} uncommitted change(s), e.g. {status[0].strip()}")

    if args.no_fetch:
        add("In sync with origin", True, "skipped (--no-fetch): not verified", warning=True)
    else:
        fetched = git(repo, "fetch", "--quiet", "origin", branch, "--tags", check=False)
        if fetched.returncode != 0:
            add("In sync with origin", False, f"could not fetch origin: {fetched.stderr.strip()[:120]}")
        else:
            pushed = git(repo, "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}", check=False).returncode == 0
            add("HEAD is pushed", pushed, "" if pushed else
                f"HEAD is not on origin/{branch}: push first, CI builds what is on the remote")

    local = git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0
    add("Tag is new locally", not local, tag if not local else f"{tag} already exists locally")
    if not args.no_fetch:
        remote = bool(git(repo, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", check=False).stdout.strip())
        add("Tag is new on origin", not remote, "" if not remote else f"{tag} already exists on origin: bump the build")

    builds = existing_builds(repo, args.tag_format, det.version)
    higher = [b for b in builds if b >= int(det.build)] if det.build.isdigit() else []
    add("Build is higher than tagged ones", not higher,
        "" if not higher else f"version {det.version} is already tagged with build(s) {higher}")

    changelog_path = repo / args.changelog
    if changelog_path.is_file():
        entries = changelog_mod.count_entries(changelog_path.read_text(encoding="utf-8"))
        add("Changelog has Unreleased entries", entries > 0,
            f"{entries} entr{'y' if entries == 1 else 'ies'}" if entries else "[Unreleased] is empty", warning=True)

    for label, command in (("Build command", args.build_cmd), ("Test command", args.test_cmd)):
        if command:
            result = subprocess.run(command, shell=True, cwd=repo, capture_output=True, text=True)
            tail = (result.stdout + result.stderr).strip().splitlines()[-3:]
            add(label, result.returncode == 0, command if result.returncode == 0 else
                f"failed ({result.returncode}): {' | '.join(tail)}")
    return checks


def print_checks(checks: list[Check]) -> bool:
    ok = True
    for c in checks:
        mark = "ok  " if c.ok else ("warn" if c.warning else "FAIL")
        if not c.ok and not c.warning:
            ok = False
        print(f"  [{mark}] {c.label:<36} {c.detail}".rstrip())
    return ok


# -- commands -----------------------------------------------------------------------------


def cmd_info(args, det: Detection, repo: Path) -> int:
    tag = tag_for(args.tag_format, det.version, det.build)
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    print(f"Project:      {det.xcodeproj.name}")
    print(f"App target:   {det.target.name}")
    print(f"Version:      {det.version}")
    print(f"Build:        {det.build}   (also set on: {', '.join(det.owners_of_build)})")
    if det.xcodebuild is not None:
        print(f"xcodebuild:   {det.xcodebuild.get(pbxproj.VERSION_KEY)} ({det.xcodebuild.get(pbxproj.BUILD_KEY)})"
              f"{'' if det.consistent else '   <-- differs'}")
    print(f"Tag for it:   {tag}")
    local = git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0
    print(f"              {'exists locally' if local else 'not created yet'}")
    last = git(repo, "describe", "--tags", "--abbrev=0", "--match", tag_glob(args.tag_format), check=False)
    if last.returncode == 0:
        name = last.stdout.strip()
        ahead = git(repo, "rev-list", "--count", f"{name}..HEAD", check=False).stdout.strip()
        print(f"Latest tag:   {name} ({ahead} commit(s) since)")
    else:
        print("Latest tag:   none matching " + tag_glob(args.tag_format))
    dirty = bool(git(repo, "status", "--porcelain").stdout.strip())
    print(f"Branch:       {branch}, {'uncommitted changes' if dirty else 'clean'}")
    path = repo / args.changelog
    if path.is_file():
        n = changelog_mod.count_entries(path.read_text(encoding="utf-8"))
        print(f"Changelog:    {args.changelog}, {n} Unreleased entr{'y' if n == 1 else 'ies'}")
    else:
        print(f"Changelog:    no {args.changelog}")
    scripts = [p for p in (".claude/skills/build.sh", ".claude/skills/test.sh", "create_tag.sh", "bump_build.sh",
                           "ci_scripts") if (repo / p).exists()]
    if scripts:
        print("Project files: " + ", ".join(scripts))
    for warning in det.warnings:
        print(f"Warning:      {warning}")
    return 0


def cmd_check(args, det: Detection, repo: Path) -> int:
    print(f"{det.target.name} {det.version} ({det.build}) -> tag {tag_for(args.tag_format, det.version, det.build)}")
    ok = print_checks(run_checks(det, repo, args))
    for warning in det.warnings:
        print(f"  note: {warning}")
    print("\nReady to tag." if ok else "\nNot ready: fix the FAIL items above.")
    return 0 if ok else 1


def cmd_bump(args, det: Detection, repo: Path) -> int:
    if not det.consistent:
        raise ReleaseError("the project file and xcodebuild disagree about the version or build; "
                           "bumping the project file would not change what is built. Resolve that first.")
    if not det.build.isdigit():
        raise ReleaseError(f"the current build {det.build!r} is not a number")
    new_build = args.build if args.build is not None else int(det.build) + 1
    if new_build <= 0:
        raise ReleaseError("the build number must be positive")
    edits = det.project.plan_change(pbxproj.BUILD_KEY, det.build, str(new_build))
    if args.version:
        if not VERSION_RE.match(args.version):
            raise ReleaseError(f"{args.version!r} is not X.Y or X.Y.Z")
        edits += det.project.plan_change(pbxproj.VERSION_KEY, det.version, args.version)

    print(f"Build:   {det.build} -> {new_build}")
    if args.version:
        print(f"Version: {det.version} -> {args.version}")
    if new_build <= int(det.build):
        print(f"Warning: {new_build} is not higher than the current {det.build}; the store needs an increase "
              "for the same version.")
    for edit in edits:
        print(f"  {edit.owner:<34} {edit.configuration:<8} {edit.key}")
    others = sorted({f"{det.project.owner_of(cid)} ({s.value})" for cid, c in det.project.configs.items()
                     for s in [c.settings.get(pbxproj.BUILD_KEY)] if s and s.value != det.build})
    if others:
        print("Left alone, they carry a different build: " + ", ".join(others))
    if not args.write:
        print("\nPreview only. Pass --write to change the project file.")
        return 0
    det.project.path.write_text(det.project.apply(edits), encoding="utf-8")
    print(f"\nWrote {len(edits)} setting(s) in {det.project.path.relative_to(repo)}. Commit it, push, then tag.")
    return 0


def cmd_changelog(args, det: Detection, repo: Path) -> int:
    version = args.version or det.version
    path = repo / args.changelog
    if not path.is_file():
        raise ReleaseError(f"{path} does not exist")
    text = path.read_text(encoding="utf-8")
    date = args.date or datetime.date.today().isoformat()
    try:
        updated = changelog_mod.rollover(text, version, date, allow_empty=args.allow_empty)
    except changelog_mod.ChangelogError as exc:
        raise ReleaseError(str(exc)) from exc
    entries = changelog_mod.count_entries(text)
    print(f"{args.changelog}: [Unreleased] ({entries} entr{'y' if entries == 1 else 'ies'}) becomes "
          f"[{version}] - {date}, and a fresh empty [Unreleased] is added above it.")
    if not args.write:
        print("Preview only. Pass --write to change the file.")
        return 0
    path.write_text(updated, encoding="utf-8")
    print("Written.")
    return 0


def confirm_tag(tag: str, assume_yes: bool) -> bool:
    """Pushing the tag starts a CI build, so a stray 'y' is not enough: the tag name has to be typed."""
    if assume_yes:
        return True
    try:
        typed = input(f"Type the tag ({tag}) to create and push it: ").strip()
    except EOFError:
        print("No interactive input available -- re-run with --yes to confirm.", file=sys.stderr)
        return False
    if typed != tag:
        print("Tag name did not match. Nothing was created.")
        return False
    return True


def cmd_tag(args, det: Detection, repo: Path) -> int:
    tag = tag_for(args.tag_format, det.version, det.build)
    print(f"{det.target.name} {det.version} ({det.build}) -> tag {tag}")
    ok = print_checks(run_checks(det, repo, args))
    if not ok:
        print("\nNot tagging: fix the FAIL items above.")
        return 1
    sha = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    if not args.push:
        print(f"\nPreview only: would create annotated tag {tag} on {sha} and push just that tag.")
        print("Pass --push to do it. Pushing the tag starts the CI build for that commit.")
        return 0
    if not confirm_tag(tag, args.yes):
        return 1
    git(repo, "tag", "-a", tag, "-m", f"Release {tag}")
    git(repo, "push", "origin", f"refs/tags/{tag}")
    print(f"\nCreated and pushed {tag} on {sha}.")
    print("Next: wait for the CI build to finish and the build to appear in App Store Connect, then attach it to")
    print("the version and send the notes. For the next delivery of this version: `bump`, commit, push, tag.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    # Options shared by every command live on a parent parser so they can be
    # written after the command, which is where people put them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="Path to the .xcodeproj (default: the only one in the current folder)")
    common.add_argument("--target", help="App target name (default: the only iOS app target)")
    common.add_argument("--configuration", default="Release", help="Build configuration to read (default: Release)")
    common.add_argument("--tag-format", default=DEFAULT_TAG_FORMAT,
                        help=f"Tag name with {{version}} and {{build}} (default: {DEFAULT_TAG_FORMAT})")
    common.add_argument("--branch", default="main", help="Branch releases are cut from (default: main)")
    common.add_argument("--any-branch", action="store_true", help="Do not require --branch")
    common.add_argument("--allow-untracked", action="store_true", help="Ignore untracked files when checking the tree")
    common.add_argument("--no-fetch", action="store_true", help="Do not contact origin (skips the remote checks)")
    common.add_argument("--no-xcodebuild", action="store_true", help="Do not call xcodebuild; read the project file only")
    common.add_argument("--changelog", default="CHANGELOG.md", help="Changelog file, relative to the repo (default: CHANGELOG.md)")
    common.add_argument("--build-cmd", help="Command that builds what will be archived; must exit 0")
    common.add_argument("--test-cmd", help="Command that runs the tests; must exit 0")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", parents=[common], help="Show version, build, tag, repo and changelog state"
                   ).set_defaults(handler=cmd_info)
    sub.add_parser("check", parents=[common], help="Run the release guards").set_defaults(handler=cmd_check)

    b = sub.add_parser("bump", parents=[common], help="Raise the build number")
    b.add_argument("--build", type=int, help="Set this build number instead of adding one")
    b.add_argument("--version", help="Also set the marketing version, X.Y.Z")
    b.add_argument("--write", action="store_true", help="Change the project file (default: preview)")
    b.set_defaults(handler=cmd_bump)

    c = sub.add_parser("changelog", parents=[common], help="Close [Unreleased] as a version")
    c.add_argument("--version", help="Version to close it as (default: the app's current version)")
    c.add_argument("--date", help="YYYY-MM-DD (default: today)")
    c.add_argument("--allow-empty", action="store_true", help="Close it even with no entries")
    c.add_argument("--write", action="store_true", help="Change the file (default: preview)")
    c.set_defaults(handler=cmd_changelog)

    t = sub.add_parser("tag", parents=[common], help="Check, then create and push the release tag")
    t.add_argument("--push", action="store_true", help="Create the tag and push it (default: preview)")
    t.add_argument("--yes", action="store_true", help="With --push: skip typing the tag name to confirm")
    t.set_defaults(handler=cmd_tag)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        xcodeproj = find_project(args.project, Path.cwd())
        repo = repo_root(xcodeproj.parent)
        det = detect(xcodeproj, args.target, args.configuration, use_xcodebuild=not args.no_xcodebuild)
        return args.handler(args, det, repo)
    except (ReleaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
