"""
AfterHour's public feed API — fetching, pagination, and post normalization.

Kept separate from the Streamlit UI so it can be imported and tested without
booting a whole app (streamlit_app.py runs its UI at import time).

No login. No API key. Public AfterHour data only.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
API_BASE = "https://api.afterhour.com/social/feed"

# The API accepts take=100, but anything past the first page at that size makes its
# gateway give up with a 504 essentially every time. 50 is the largest size that
# paginates reliably; smaller sizes are the fallback when even that times out.
PAGE_SIZE = 50
MIN_PAGE_SIZE = 10

FUNNEL_KEYWORDS = re.compile(
    r"\b(discord|substack|coaching|mentorship|whop|patreon|paid tier|paid group|"
    r"paid community|subscription|discount code|promo code|referral|coupon|"
    r"join my|link in bio|dm me for access|waitlist|lifetime access)\b", re.I)


def _get(url: str, retries: int = 6, timeout: int = 60):
    """AfterHour's gateway throws intermittent 504s under load — roughly one request
    in seven, even at a page size it can otherwise handle. They clear on their own,
    so back off and retry rather than failing the whole run."""
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
                ct = r.headers.get("content-type", "")
            return json.loads(body) if ct.startswith("application/json") else body
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(min(1.5 * (2 ** attempt), 12))


def profile_id(username: str) -> str:
    """Your username isn't what the API uses internally — dig your prf_ id out of
    your profile page's embedded Next.js data first."""
    try:
        html = _get(f"https://afterhour.com/{username}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise LookupError(
                f"'{username}' doesn't seem to exist on AfterHour. Check the spelling "
                f"matches afterhour.com/{username} exactly (case-sensitive)."
            ) from None
        raise
    if isinstance(html, dict):
        raise LookupError("Got an unexpected response — AfterHour's site may have changed.")

    # The id and the username sit near each other in the payload, but the order
    # isn't guaranteed — try it both ways round.
    user = re.escape(username)
    id_then_name = r'"id":"(prf_[a-f0-9]+)".{0,400}?"username":"' + user + r'"'
    name_then_id = r'"username":"' + user + r'".{0,400}?"id":"(prf_[a-f0-9]+)"'
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S):
        chunk = json.loads('"' + m.group(1) + '"')
        found = (re.search(id_then_name, chunk, re.I | re.S)
                 or re.search(name_then_id, chunk, re.I | re.S))
        if found:
            return found.group(1)
    raise LookupError(f"Couldn't find a profile id for '{username}'.")


def fetch_all_posts(author_id: str, progress_cb=None) -> list[dict]:
    """Page through the feed with `cursor` until we've seen every post.

    The cursor is independent of `take`, so if a page keeps timing out we can retry
    the very same cursor at a smaller size and pick up exactly where we left off.
    """
    posts: list[dict] = []
    cursor = None
    total = None
    take = PAGE_SIZE

    while True:
        url = f"{API_BASE}?take={take}&contentTypes=post&authorId={author_id}"
        if cursor is not None:
            url += f"&cursor={cursor}"

        try:
            page = _get(url)
        except urllib.error.HTTPError as e:
            # Still timing out after all the retries — shrink the page and try the
            # same cursor again. Only give up once we're already as small as we go.
            if e.code >= 500 and take > MIN_PAGE_SIZE:
                take = max(take // 2, MIN_PAGE_SIZE)
                continue
            raise

        if total is None:
            total = page.get("totalCount", 0)
        batch = page.get("items", [])
        if not batch:
            break
        posts.extend(batch)
        if progress_cb:
            progress_cb(len(posts), total)
        if len(posts) >= total:
            break
        cursor = page.get("cursor")
        if cursor is None:
            break
        time.sleep(0.2)
    return posts


def normalize(item: dict) -> dict:
    post = item.get("post") or {}
    snapshot = item.get("portfolioSnapshot") or {}
    total_value = snapshot.get("totalValue")

    tag = (item.get("primaryTopicKey") or "").capitalize()
    gain_loss = tag if tag in ("Gain", "Loss") else ""

    body = post.get("body", "")
    tweet_source = ""
    if not body:
        tweets = item.get("tweets") or []
        if tweets:
            body = tweets[0].get("text", "")
            tweet_source = tweets[0].get("username", "")

    tickers = [s.get("tickerSymbol") for s in (item.get("securities") or []) if s and s.get("tickerSymbol")]
    link_urls = [lp.get("url", "") for lp in (item.get("linkPreviews") or []) if lp.get("url")]
    created_at = post.get("createdAt") or item.get("createdAt") or ""

    return {
        "date": created_at[:10],
        "created_at": created_at,
        "tag": tag,
        "gain_loss": gain_loss,
        "amount_k": total_value / 1000 if total_value is not None else None,
        "tickers": ",".join(tickers),
        "title": post.get("title", ""),
        "body": body,
        "embedded_tweet_from": tweet_source,
        "is_hunt_post": bool(item.get("isHuntPost")),
        "comment_count": item.get("commentCount", 0),
        "reaction_count": sum((item.get("reactionCounts") or {}).values()),
        "view_count": item.get("viewCount", 0),
        "id": post.get("id") or item.get("id"),
        "share_url": post.get("shareUrl", ""),
        "link_urls": ",".join(link_urls),
    }
