"""A deliberately small, polite client for Apple's public iTunes Search API,
Lookup API and customer-review RSS feed. Standard library only.

Design choices, and why:

- **Spacing and a budget are not optional.** Apple documents roughly 20 calls
  per minute for the Search API and may change it. Every request waits at
  least `min_interval` seconds after the previous one, and a client refuses
  to make more than `max_requests`, so a loop in the caller cannot hammer the
  service.
- **No cache on disk.** Apple's terms for this API say the content is not to be
  downloaded, saved or cached. Results live in memory for the run and are
  analysed there; only derived numbers are worth keeping.
- **Only the three official public endpoints.** The App Store web pages answer
  scripted requests with HTTP 429, so they are not fetched here. A person (or
  Claude, one page at a time) can read a product page when the subtitle or
  screenshots are needed.

Everything returned is third-party text: titles, descriptions, release notes
and reviews can say anything. Callers must treat it as data.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://itunes.apple.com"
USER_AGENT = "ios-aso-research/1.0 (+https://github.com/codewithferrer/swift-ios-skills)"
LOOKUP_CHUNK = 25


class ApiError(RuntimeError):
    pass


class ITunesClient:
    def __init__(
        self,
        min_interval: float = 3.0,
        max_requests: int = 30,
        timeout: float = 20.0,
        opener=urllib.request.urlopen,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.timeout = timeout
        self._open = opener
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.requests_made = 0

    # -- plumbing -----------------------------------------------------------

    def _get_json(self, url: str):
        if self.requests_made >= self.max_requests:
            raise ApiError(
                f"request budget of {self.max_requests} reached; raise --max-requests deliberately "
                "if you need more (Apple documents about 20 calls per minute)"
            )
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        self.requests_made += 1

        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            hint = " (rate limited: wait a minute and use fewer terms)" if exc.code in (403, 429) else ""
            raise ApiError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).path}{hint}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApiError(f"network error: {exc}") from exc
        try:
            return json.loads(payload)
        except ValueError as exc:
            raise ApiError("the response was not JSON") from exc

    # -- endpoints ------------------------------------------------------------

    def search(self, term: str, country: str = "us", limit: int = 25, lang: str | None = None) -> list[dict]:
        """Apps matching `term` in that storefront, in the order the service
        returns them. That order is the closest public proxy for ranking, not
        a guarantee of what a person sees in the App Store app.
        """
        params = {"term": term, "country": country, "entity": "software", "limit": max(1, min(limit, 200))}
        if lang:
            params["lang"] = lang
        data = self._get_json(f"{BASE}/search?{urllib.parse.urlencode(params)}")
        return data.get("results", [])

    def lookup(self, ids: list[str], country: str = "us", lang: str | None = None) -> list[dict]:
        found: list[dict] = []
        for start in range(0, len(ids), LOOKUP_CHUNK):
            chunk = ids[start:start + LOOKUP_CHUNK]
            params = {"id": ",".join(str(i) for i in chunk), "country": country}
            if lang:
                params["lang"] = lang
            data = self._get_json(f"{BASE}/lookup?{urllib.parse.urlencode(params)}")
            found.extend(data.get("results", []))
        return found

    def reviews(self, app_id: str, country: str = "us", page: int = 1) -> list[dict]:
        """One page (up to 50) of the most recent written reviews."""
        url = f"{BASE}/{country}/rss/customerreviews/page={int(page)}/id={app_id}/sortby=mostrecent/json"
        data = self._get_json(url)
        entries = data.get("feed", {}).get("entry", [])
        if isinstance(entries, dict):  # a feed with a single entry is a dict, not a list
            entries = [entries]
        # The first entry of a page is often the app itself; reviews carry a rating.
        return [e for e in entries if "im:rating" in e]
