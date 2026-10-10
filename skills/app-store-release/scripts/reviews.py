#!/usr/bin/env python3
"""Read an app's written customer reviews and manage public replies.

    list          Read-only. Reviews newest first, with any reply under each.
    reply         Post a public reply under a review.
    delete-reply  Remove a public reply (the API cannot edit one).

A reply is public text published under your name, so `reply` and
`delete-reply` do nothing by default: they print what they would do, and
only act when --send / --delete is passed. Posting also asks for a
confirmation unless --yes is given.

Only written reviews are exposed by the API; star-only ratings are not.

Usage:
    python reviews.py list --app my-app
    python reviews.py list --app my-app --unanswered --rating 1 --territory USA
    python reviews.py list --app my-app --summary
    python reviews.py reply --review 00000000-0000-0000-0000-000000000000 --body-file reply.txt
    python reviews.py reply --review <id> --body-file reply.txt --send
    python reviews.py delete-reply --response <id> --delete
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from asc_common import ConfigError, add_app_arguments, confirm, load_client_module, resolve_app
from asc_status import day, render_review


def cmd_list(args, asc) -> int:
    app = resolve_app(args)
    client = asc.AppStoreConnectClient()
    reviews, responses = client.list_customer_reviews(
        app.app_id,
        limit=args.limit,
        rating=args.rating,
        territory=args.territory,
        unanswered=args.unanswered,
    )

    print(f"App: {app.key} (id {app.app_id})")
    if not reviews:
        print("No written reviews match." if (args.unanswered or args.rating or args.territory)
              else "No written reviews yet.")
    else:
        ratings = [r["attributes"].get("rating") for r in reviews if r["attributes"].get("rating")]
        average = f", average {sum(ratings) / len(ratings):.1f}/5" if ratings else ""
        print(f"{len(reviews)} review(s), newest first{average}\n")
        print("\n\n".join(render_review(review, responses, args.body_chars) for review in reviews))

    if args.summary:
        print()
        try:
            summaries = client.list_review_summarizations(app.app_id)
        except asc.AppStoreConnectError as exc:
            print(f"Review summaries not available: {exc}")
            summaries = None
        if summaries is not None and not summaries:
            print("No review summaries available for this app yet.")
        for summary in summaries or []:
            a = summary["attributes"]
            print(f"Summary ({a.get('locale')}, {day(a.get('createdDate'))}):")
            print("  " + (a.get("text") or "").strip().replace("\n", "\n  "))
    return 0


def read_body(args) -> str:
    if args.body and args.body_file:
        raise ConfigError("Pass either --body or --body-file, not both.")
    if args.body_file:
        text = Path(args.body_file).expanduser().read_text(encoding="utf-8")
    elif args.body:
        text = args.body
    else:
        raise ConfigError("A reply needs --body or --body-file.")
    text = text.strip()
    if not text:
        raise ConfigError("The reply text is empty.")
    return text


def cmd_reply(args, asc) -> int:
    body = read_body(args)
    client = asc.AppStoreConnectClient()

    try:
        review = client.get_customer_review(args.review)
    except asc.AppStoreConnectError as exc:
        if exc.status_code == 404:
            print(f"No review with id {args.review}. Copy the id from `reviews.py list`.", file=sys.stderr)
            return 1
        raise
    a = review["attributes"]
    print(f"Review {args.review}: [{a.get('rating')}/5] {day(a.get('createdDate'))} {a.get('territory')} "
          f"\"{(a.get('title') or '').strip()}\"")
    existing = client.get_review_response(args.review)
    if existing is not None:
        print(f"This review already has a reply (response id {existing['id']}, "
              f"{existing['attributes'].get('state')}).", file=sys.stderr)
        print("The API cannot edit a reply: run `delete-reply` for it first, then post the new one.", file=sys.stderr)
        return 1

    print("\nPublic reply that would be posted:")
    print("  " + body.replace("\n", "\n  "))
    print(f"\n({len(body)} characters)")

    if not args.send:
        print("\nPreview only -- nothing was posted. Pass --send to publish it.")
        return 0
    if not confirm("Post this public reply now?", args.yes):
        return 1

    response = client.create_review_response(args.review, body)
    print(f"Posted (response id {response['id']}, state {response['attributes'].get('state')}).")
    return 0


def cmd_delete_reply(args, asc) -> int:
    if not args.delete:
        print(f"Would delete the public reply {args.response}.")
        print("Preview only -- nothing was deleted. Pass --delete to remove it.")
        return 0
    if not confirm(f"Delete the public reply {args.response} now?", args.yes):
        return 1
    asc.AppStoreConnectClient().delete_review_response(args.response)
    print("Deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    lst = sub.add_parser("list", help="List written reviews, newest first")
    add_app_arguments(lst)
    lst.add_argument("--unanswered", action="store_true", help="Only reviews with no published reply")
    lst.add_argument("--rating", type=int, choices=range(1, 6), help="Only this star rating")
    lst.add_argument("--territory", help="Only this territory, a three-letter code such as USA or ESP")
    lst.add_argument("--limit", type=int, default=50, help="How many reviews to fetch (max 200, default 50)")
    lst.add_argument("--body-chars", type=int, default=500, help="Truncate long review text (default 500)")
    lst.add_argument("--summary", action="store_true", help="Also show Apple's review summaries, if any")
    lst.set_defaults(handler=cmd_list)

    rep = sub.add_parser("reply", help="Post a public reply under a review")
    rep.add_argument("--review", required=True, help="Review id, as shown by `list`")
    rep.add_argument("--body", help="Reply text")
    rep.add_argument("--body-file", help="File holding the reply text")
    rep.add_argument("--send", action="store_true", help="Actually post the reply (default: preview only)")
    rep.add_argument("--yes", action="store_true", help="Skip the confirmation prompt (--send is still required)")
    rep.set_defaults(handler=cmd_reply)

    dele = sub.add_parser("delete-reply", help="Delete a public reply")
    dele.add_argument("--response", required=True, help="Response id, as shown by `list`")
    dele.add_argument("--delete", action="store_true", help="Actually delete it (default: preview only)")
    dele.add_argument("--yes", action="store_true", help="Skip the confirmation prompt (--delete is still required)")
    dele.set_defaults(handler=cmd_delete_reply)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        asc = load_client_module()
        return args.handler(args, asc)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    except asc.AppStoreConnectError as exc:
        print(f"App Store Connect API error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
