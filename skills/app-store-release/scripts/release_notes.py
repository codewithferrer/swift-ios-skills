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


# -- Pre-flight checks --------------------------------------------------------

WHATS_NEW_MAX_CHARS = 4000

# Characters that render as emoji by default. A heuristic rather than the full
# Unicode emoji set: it deliberately leaves symbols such as arrows, check marks
# (U+2713) and (c)/TM alone, which are legitimate in release notes.
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\uFE0F\u231A\u231B\u23E9-\u23F3\u23F8-\u23FA\u25FD\u25FE"
    "\u2614\u2615\u2648-\u2653\u267F\u2693\u26A1\u26AA\u26AB\u26BD\u26BE\u26C4\u26C5"
    "\u26CE\u26D4\u26EA\u26F2\u26F3\u26F5\u26FA\u26FD\u2705\u270A\u270B\u2728\u274C"
    "\u274E\u2753-\u2755\u2757\u2795-\u2797\u27B0\u27BF\u2B1B\u2B1C\u2B50\u2B55]"
)


def find_emoji(text: str) -> list[str]:
    return sorted(set(EMOJI_RE.findall(text)))


def check(texts: dict[str, str], allow_emoji: bool = False) -> list[str]:
    """Problems that would make App Store Connect refuse the text (or that
    have been seen to), found before any API call. Empty list means fine.
    """
    problems = []
    for locale in sorted(texts):
        text = texts[locale]
        if len(text) > WHATS_NEW_MAX_CHARS:
            problems.append(f"{locale}: {len(text)} characters, the limit is {WHATS_NEW_MAX_CHARS}")
        if not allow_emoji:
            found = find_emoji(text)
            if found:
                # U+FE0F alone (e.g. a red heart) has nothing printable to show.
                shown = " ".join(c for c in found if c != "\ufe0f") or "emoji variation selector"
                problems.append(
                    f"{locale}: contains emoji ({shown}); App Store Connect has been seen rejecting "
                    "them in What's New -- remove them, or pass --allow-emoji to send anyway"
                )
    return problems
