#!/usr/bin/env python3
"""Audit the search-relevant text of an iOS listing, offline, from a metadata
folder (name.txt, subtitle.txt, keywords.txt, promotional_text.txt and
description.txt per locale folder, the layout fastlane and the
app-store-release skill's pull_metadata.py use).

Reports, per locale, how much of each character budget is used and the
deterministic problems: spaces around commas, repeated and plural words,
keywords already covered by the name or subtitle, claims in the name. Then,
per storefront, which localizations feed it and where the same word is
spent twice.

It cannot know search volume or rankings. See references/measurement.md for
where that data comes from.

Usage:
    python aso_audit.py --dir metadata
    python aso_audit.py --dir metadata --storefronts US,ES,MX
    python aso_audit.py --dir metadata --category Productivity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aso_logic import (
    audit,
    default_storefronts,
    load_listings,
    load_storefronts,
    render_fields,
    render_findings,
)

DEFAULT_TABLE = Path(__file__).resolve().parent.parent / "references" / "storefront-locales.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", default="metadata", help="Metadata folder (default: ./metadata)")
    parser.add_argument("--storefronts", help="Comma-separated storefront codes, e.g. US,ES,MX "
                        "(default: every storefront whose default locale you have)")
    parser.add_argument("--category", help="The app's primary category name, to flag it appearing in keywords")
    parser.add_argument("--storefront-table", default=str(DEFAULT_TABLE),
                        help="JSON table of storefront localizations (default: the one shipped in references/)")
    args = parser.parse_args()

    try:
        listings = load_listings(Path(args.dir).expanduser())
        table = load_storefronts(Path(args.storefront_table))
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(exc, file=sys.stderr)
        return 1
    if not listings:
        print(f"No locale folders with metadata files found in {args.dir}.", file=sys.stderr)
        return 1

    if args.storefronts:
        codes = [c.strip().upper() for c in args.storefronts.split(",") if c.strip()]
        unknown = [c for c in codes if c not in table]
        if unknown:
            print(f"Not in the storefront table: {', '.join(unknown)}. Known: {', '.join(table)}", file=sys.stderr)
            return 1
    else:
        codes = default_storefronts(listings, table)

    print(f"Locales: {', '.join(sorted(listings))}")
    print(f"Storefronts audited: {', '.join(codes) or 'none (no default locale matches)'}")
    print()
    print(render_fields(listings))
    print()
    print(render_findings(audit(listings, table, codes, args.category)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
