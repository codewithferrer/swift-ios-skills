# Conventions and design notes

## Why the version is read from the target, not from the file

`grep MARKETING_VERSION project.pbxproj | head -1` returns whichever of many
matches comes first. A typical app project holds the setting once per target and
per configuration, so a project with an iOS app, a watch app, a widget and two
test targets has a dozen of them, with the tests and the watch app often left at
`1.0` while the app is on `1.9.2`. The first match depends on the order Xcode
wrote the file. This tool resolves a setting the way the build does: the
target's own value for the configuration, else the project's.

It cannot follow `.xcconfig` files or `$(inherited)` chains. That is why it also
asks `xcodebuild -showBuildSettings` when available and treats a disagreement as
a blocker for `bump`.

## How a bump picks lines

1. Read the app's build number (say 22).
2. Find every build configuration that sets `CURRENT_PROJECT_VERSION` to exactly
   22.
3. Change those lines, and only the value on them.

Targets that carry the same number are the ones that ship with the app: the
watch app, widgets and other extensions are expected to have the same build as
the app, and a mismatch is a classic upload validation failure. Targets with a
different number (tests, usually `1`) are not touched. The preview names both
groups before anything is written.

Edits are made on the original lines, so the rest of the project file is left
byte for byte as it was and the diff is only the lines that carry the number.

## The tag

`release.<version>.<build>`. The build is part of the name on purpose: the same
version is delivered more than once (a rejected build, a fix) and each delivery
needs its own tag and a higher build.

Only that tag is pushed (`git push origin refs/tags/<tag>`), never `--tags`:
pushing every local tag would also start CI for anything else that happens to be
lying around.

CI (Xcode Cloud, in the setup this was written for) is configured to start on
tags matching `release.*`; the tag format and that trigger have to agree.

## The checks, and why each exists

| Check | Why |
|-------|-----|
| Version format and numeric build | A malformed value makes a malformed tag |
| Settings agree with xcodebuild | The file might not be the truth |
| On the release branch | Tags cut from a feature branch ship unmerged work |
| Working tree clean | A tag should name a commit that is the whole story |
| HEAD is on origin | CI builds the remote, not your machine |
| Tag is new locally and on origin | Re-using a tag silently ships the old commit |
| Build higher than tagged ones | The store needs an increase within a version |
| Changelog has entries (warning) | A release with nothing to say usually means the wrong branch |
| Build and test commands | What will be archived should compile and pass |

## Build and test commands for the check

`--build-cmd` and `--test-cmd` are run through the shell from the repository
root and judged by exit status alone. A common way to make `xcodebuild` quiet is
to filter it:

```bash
xcodebuild ... build 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)"
```

The exit status of that pipeline is `grep`'s, and `grep` succeeds when it finds
the line `BUILD FAILED`. Such a script therefore looks like a pass for a broken
build. Put `set -o pipefail` at the top of the script: the pipeline then returns
`xcodebuild`'s failure status while `grep` still shortens the output. (Or pass
the unfiltered `xcodebuild` command to `--build-cmd`; the check prints the last
lines of output when it fails.)

## Replacing a project's own `create_tag.sh`

Per-project tag scripts drift: one project gets the guards and the others keep an
older copy. The usual older form takes the first `MARKETING_VERSION` and
`CURRENT_PROJECT_VERSION` in the project file and runs `git push --tags`. That
works while the first match happens to be the app's, and it pushes every local
tag, which can start CI for tags that were never meant to ship.

To use this skill instead, leave a thin wrapper where the script was, with the
build command your project already uses:

```bash
#!/bin/bash
# Cut the release tag. All the logic lives in the ios-release-train skill.
exec python3 ~/.claude/skills/ios-release-train/scripts/release_train.py tag \
    --build-cmd "<your build command>" "$@"
```

Run it with no arguments for the preview and with `--push` to release; the tag
name is typed to confirm. Doing it repository by repository keeps each change
easy to review.
