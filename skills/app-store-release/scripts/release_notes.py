"""Parses a RELEASE_NOTES.md file into {locale: text}.

Expected format (see examples/RELEASE_NOTES.example.md for a full example):

    ## en-US (English)

    ```
    What's new text goes here, possibly spanning
    several lines.
    ```

    ## es-ES / es-MX (Spanish)

    ```
    The same text for two App Store Connect locales at once.
    ```

The parenthesised language name after the locale codes is required (it is
what marks a heading as a locale heading) but its content is ignored.

A heading is only treated as a locale block if the next non-blank line
opens a fenced code block -- this is what lets a trailing reference
section (plain prose, no fence) be ignored without needing to special-case
the "---" separator that precedes it.

Locale codes are read directly from the heading, not guessed from the
language name -- "es-ES / es-MX" expands to two locales with identical
text, matching how App Store Connect treats them as separate
localizations that happen to usually want the same copy.
"""

from __future__ import annotations

import re
from pathlib import Path

HEADING_RE = re.compile(r"^##\s+([A-Za-z0-9/\-\s]+?)\s*\(")
FENCE_RE = re.compile(r"^```")


def parse(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist -- draft release notes for this app/version first."
        )

    lines = path.read_text().splitlines()
    result: dict[str, str] = {}
    i = 0
    while i < len(lines):
        heading = HEADING_RE.match(lines[i])
        if not heading:
            i += 1
            continue

        locale_codes = [code.strip() for code in heading.group(1).split("/") if code.strip()]

        # Look ahead past blank lines for a fence; if it's not there, this
        # heading belongs to the trailing reference section, not a locale.
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or not FENCE_RE.match(lines[j]):
            i += 1
            continue

        body_start = j + 1
        body_end = body_start
        while body_end < len(lines) and not FENCE_RE.match(lines[body_end]):
            body_end += 1
        text = "\n".join(lines[body_start:body_end]).strip()

        for code in locale_codes:
            result[code] = text

        i = body_end + 1

    return result
