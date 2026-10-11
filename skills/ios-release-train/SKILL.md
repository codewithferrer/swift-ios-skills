---
name: ios-release-train
description: Cut an iOS release from the repository side - find the app's real version and build number (not the first match in the project file), check the repo is ready to ship, bump the build number only on the targets that ship, close the changelog, and create and push the release tag that starts the CI build (Xcode Cloud) - with previews by default and nothing pushed without an explicit flag. Use whenever the user wants to release or ship a version, bump the build, tag a release, close or roll over the changelog, check whether a repo is ready to tag, or asks what version and build an iOS app is on, even if they never say release train.
compatibility: Python 3.9+ and git; xcodebuild is used to confirm the values when present. No App Store Connect access and no credentials; the only network is git talking to origin.
---

# iOS release train

The repository half of shipping an iOS version: which version and build the
app is on, whether the repo is in a state worth tagging, the build bump, the
changelog, and the tag that makes CI build and upload. It stops at "tag
pushed". What happens in App Store Connect after that (attaching the build,
release notes, listing, review) is a different job; if the `app-store-release`
skill is installed, hand off to it.

## The flow

| Step | Command | Changes anything? |
|------|---------|-------------------|
| 1 | `release_train.py info` | No. Version, build, where the build lives, tag, repo state |
| 2 | `release_train.py bump` then `bump --write` | `--write` edits the project file |
| 3 | commit the bump and push (git, by the user or on their request) | |
| 4 | `release_train.py check [--build-cmd C] [--test-cmd C]` | No |
| 5 | `release_train.py tag` then `tag --push` | `--push` asks for the tag name to be typed, then creates and pushes it |
| 6 | (after the build ships) `release_train.py changelog --version V --write` | `--write` edits the changelog |

Run the commands from the project folder (where the `.xcodeproj` is), or pass
`--project`. Options such as `--no-fetch` or `--build-cmd` go after the command.

## Safety rules

Pushing a tag starts a CI build and sends a build to App Store Connect. It is
cheap to start and awkward to take back, so:

- **Preview first.** `bump`, `changelog` and `tag` only describe what they
  would do until `--write` or `--push` is passed. Show the user the preview.
- **Never push a tag on your own initiative.** Run `tag --push` only when the
  user has asked to release that version, and name the tag in your reply. The
  command makes whoever runs it type the tag name; `--yes` skips that, so add
  it only when the user has asked for that tag in this conversation.
- **Do not work around a failing check.** The checks exist because CI builds
  what is on the remote. If one fails, say which and why, and let the user
  decide. `--any-branch` and `--allow-untracked` are for when the user says so.
- **Use the project's own build and test commands** for `--build-cmd` and
  `--test-cmd` (the project's `CLAUDE.md` usually names them). The point of the
  check is that what will be archived compiles and passes. Only the command's
  exit status counts, so a script that pipes `xcodebuild` through `grep` to
  shorten the output reports grep's status, and a failed build can look fine
  (see `references/conventions.md`).

## What it knows that a `grep` does not

- **The app's version, not the first one in the file.** A project with a watch
  app, widgets and test targets has the version and build in many places with
  different values. The tool ties each setting to its target and configuration
  and reads the iOS app target's.
- **Which targets share the build.** The watch app and extensions ride along
  with the app and must carry the same build number, so a bump moves them
  together and leaves alone targets that carry a different one (tests).
- **Whether the file or the toolchain is the truth.** It compares the project
  file with `xcodebuild -showBuildSettings`. If they disagree (an `.xcconfig`
  overrides them) it refuses to bump, because editing the file would not change
  what is built.

## Conventions it follows (and where to change them)

- **Tag: `release.<version>.<build>`**, for example `release.1.9.3.27`. Change it
  with `--tag-format`, using `{version}` and `{build}`.
- **The build number only goes up** within a version. A tag that already exists,
  or a lower build than one already tagged, fails the check.
- **The changelog is closed when the version is published**, not when it is
  tagged: `[Unreleased]` becomes `## [X.Y.Z] - date` and a fresh empty
  `[Unreleased]` is left above it. A flat bullet list and `###` categories both
  work; nothing else in the file is touched.

## Gotchas

- **Unpushed commits are the most common failure.** The check fails when HEAD is
  not on `origin/<branch>`; CI would build the older commit. Say so plainly.
- **Untracked files fail "working tree clean"** (a stray `metadata/` folder, for
  example). That is deliberate; `--allow-untracked` relaxes it.
- **`check` and `tag` contact origin** to fetch and to see remote tags. Use
  `--no-fetch` offline; the remote checks are then skipped and reported as such.
- **Two-component versions** (`1.0`) are accepted, and make tags like
  `release.1.0.1`.
- **Several iOS app targets** make it stop and list them; pass `--target`.
- **No changelog is fine**: that check simply does not apply.

## Reference files

| File | Read it when |
|------|--------------|
| `references/conventions.md` | Explaining why a check exists, changing the tag format, or replacing a project's old `create_tag.sh` with a call to this |
