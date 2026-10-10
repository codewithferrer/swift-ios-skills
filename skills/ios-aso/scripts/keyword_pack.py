#!/usr/bin/env python3
"""Pack a ranked list of candidate keywords into the 100-character keyword
field, skipping words Apple already indexes elsewhere in the storefront.

Counting characters by hand is where keyword fields go wrong, and so is
repeating a word that the name or subtitle already covers. This does both
deterministically: it fills the field in the order you give (first = most
important), and tells you what it skipped and why, and what did not fit.

The order of the candidates is the priority. Ranking them is the judgement
call; see references/keyword-strategy.md.

Usage:
    python keyword_pack.py --candidates "clock,morning,heavy sleeper,qr,nfc,steps" \\
        --name "Brand: Alarm Challenge" --subtitle "Wake up on the first try"
    python keyword_pack.py --candidates-file ranked.txt --name "..." --subtitle "..." \\
        --also-indexed "alarma despertador reloj"
    python keyword_pack.py --candidates "..." --keep-phrases
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aso_logic import KEYWORDS_LIMIT, pack_keywords, words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", help="Comma-separated candidates, most important first")
    parser.add_argument("--candidates-file", help="File with one candidate per line, most important first")
    parser.add_argument("--name", default="", help="The localized app name (its words are already indexed)")
    parser.add_argument("--subtitle", default="", help="The localized subtitle (its words are already indexed)")
    parser.add_argument("--also-indexed", default="", help="Other text already indexed for the same storefront, "
                        "e.g. the keywords of another localization that feeds it")
    parser.add_argument("--limit", type=int, default=KEYWORDS_LIMIT, help=f"Character budget (default: {KEYWORDS_LIMIT})")
    parser.add_argument("--keep-phrases", action="store_true",
                        help="Keep multi-word candidates whole instead of splitting them into words")
    args = parser.parse_args()

    candidates: list[str] = []
    if args.candidates_file:
        try:
            candidates += [l.strip() for l in Path(args.candidates_file).expanduser().read_text(encoding="utf-8").splitlines()]
        except OSError as exc:
            print(exc, file=sys.stderr)
            return 1
    if args.candidates:
        candidates += [c.strip() for c in args.candidates.split(",")]
    candidates = [c for c in candidates if c]
    if not candidates:
        print("Give --candidates or --candidates-file.", file=sys.stderr)
        return 1

    already = set(words(args.name)) | set(words(args.subtitle)) | set(words(args.also_indexed))
    result = pack_keywords(candidates, already, args.limit, split_phrases=not args.keep_phrases)

    print(f"Field ({result.length}/{args.limit} characters, {args.limit - result.length} free):")
    print(f"  {result.field}")
    if result.skipped:
        print("\nSkipped:")
        for word, reason in result.skipped:
            print(f"  {word}: {reason}")
    if result.did_not_fit:
        print("\nDid not fit in the space that was left:")
        print("  " + ", ".join(result.did_not_fit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
