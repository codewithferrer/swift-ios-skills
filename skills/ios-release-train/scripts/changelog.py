"""Closing a version in a Keep a Changelog style CHANGELOG.md. Pure logic.

The shape it expects is the one in use across a mix of projects: a
`## [Unreleased]` heading followed by entries (a flat bullet list, or grouped
under `###` categories; both work), then older `## [X.Y.Z] - date` sections.
Closing a version renames what is under Unreleased to that version and leaves
a fresh, empty Unreleased above it. Nothing else in the file is touched.
"""

from __future__ import annotations

import re

UNRELEASED = re.compile(r"^##[ \t]*\[Unreleased\][ \t]*$", re.IGNORECASE | re.MULTILINE)
SECTION = re.compile(r"^##[ \t]+\[", re.MULTILINE)
ENTRY = re.compile(r"^[ \t]*[-*+][ \t]+\S", re.MULTILINE)


class ChangelogError(ValueError):
    pass


def unreleased_body(text: str) -> str | None:
    """The text under `## [Unreleased]`, up to the next version heading, or
    None when there is no such heading.
    """
    head = UNRELEASED.search(text)
    if not head:
        return None
    nxt = SECTION.search(text, head.end())
    return text[head.end(): nxt.start() if nxt else len(text)]


def count_entries(text: str) -> int:
    body = unreleased_body(text)
    return len(ENTRY.findall(body)) if body else 0


def rollover(text: str, version: str, date: str, allow_empty: bool = False) -> str:
    head = UNRELEASED.search(text)
    if not head:
        raise ChangelogError("no `## [Unreleased]` heading found")
    if re.search(rf"^##[ \t]+\[{re.escape(version)}\]", text, re.MULTILINE):
        raise ChangelogError(f"a section for {version} already exists")
    if count_entries(text) == 0 and not allow_empty:
        raise ChangelogError("[Unreleased] has no entries, so there is nothing to publish "
                             "(pass --allow-empty to close it anyway)")
    before, after = text[: head.start()], text[head.end():]
    # `after` starts with the newline that ends the old heading, so the spacing
    # the file already had under it (a blank line before `### Added`, or none
    # before a bullet) carries over to the new version heading unchanged.
    return f"{before}## [Unreleased]\n\n## [{version}] - {date}{after}"
