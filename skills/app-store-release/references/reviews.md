# Customer reviews

Reading reviews and answering them in public, through `reviews.py`.

## What the API gives you

- **Written reviews only.** Star-only ratings are not exposed. An app can have
  many ratings and zero reviews here.
- **One public reply per review.** It starts as `PENDING_PUBLISH` and Apple
  publishes it shortly after.
- **No editing.** To change a reply, delete it (`delete-reply --delete`) and
  post a new one.
- **Summaries** (`list --summary`): Apple's own digest of recent reviews per
  locale, when it has produced one. Useful for spotting a theme before reading
  individual reviews.

## Procedure

1. List what is waiting:
   ```bash
   python3 scripts/reviews.py list --app my-app --unanswered
   ```
   Narrow with `--rating 1`, `--territory USA`, or `--limit`. Long review text
   is truncated; `--body-chars 2000` shows more.
2. Read them before writing anything. With several reviews, group them by
   theme first: a bug three people report deserves one considered answer
   style, not three improvised ones.
3. Draft **one reply per review** and show it right next to the review it
   answers, with the review id.
4. Wait for the user's approval of each reply. A reply is public, permanent
   until deleted, and published under their name.
5. Preview, then post:
   ```bash
   python3 scripts/reviews.py reply --review <id> --body-file reply.txt
   python3 scripts/reviews.py reply --review <id> --body-file reply.txt --send
   ```
   The preview stops if the review already has a reply, which it will name.

## Writing the reply

- **Answer in the language of the review**, not the app's default.
- **Short.** Two to four sentences. Thank them for the specific thing they
  mentioned, then answer that thing.
- **Specific, not templated.** Quote or paraphrase what they said so it is
  clear a person read it. Avoid a stock paragraph pasted under every review.
- **Bugs and requests:** acknowledge, and point to the support route the app
  already publishes (the listing's support URL or email, readable with
  `pull_metadata.py`). Never invent an address.
- **Negative reviews:** stay calm and brief. Do not argue, defend, or explain
  at length. If they are right, say so.
- **No promises** about features or dates unless the user has said that is
  real and public.
- **Never ask the reviewer to change their rating**, offer anything in
  exchange for one, or hint at it. Apple treats manipulating reviews as a
  violation.
- **No personal data.** The reviewer's nickname is public; anything beyond it
  does not belong in the reply, and nothing from a review should be copied
  outside this task.
- **No emoji** unless the app's own voice uses them and the user wants that.

## Triage notes for the user

When reporting back, keep it short: how many reviews, the average, the themes,
and which ones you drafted replies for. If something in a review looks like a
real defect (a crash, data loss, a paywall problem), say so plainly even if you
are only asked about replies; that is information the user needs.
