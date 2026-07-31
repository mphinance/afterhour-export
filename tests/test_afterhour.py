"""
Tests for the feed client and post normalization.

Everything here stubs out `_get` — no network, so CI doesn't depend on AfterHour
being up (or on it being in one of its 504 moods).
"""
from __future__ import annotations

import urllib.error

import pytest

import afterhour
from afterhour import FUNNEL_KEYWORDS, MIN_PAGE_SIZE, PAGE_SIZE, fetch_all_posts, normalize


def make_api(total, chokes_above=None, always_504=False, calls=None):
    """A fake feed endpoint that pages by cursor and can refuse large pages the way
    the real one does."""
    def fake_get(url, **kwargs):
        take = int(url.split("take=")[1].split("&")[0])
        if calls is not None:
            calls.append(take)
        if always_504 or (chokes_above is not None and take > chokes_above):
            raise urllib.error.HTTPError(url, 504, "Gateway Timeout", {}, None)
        offset = int(url.split("cursor=")[1].split("&")[0]) if "cursor=" in url else 0
        items = [{"post": {"id": f"p{i}"}} for i in range(offset, min(offset + take, total))]
        return {"totalCount": total, "items": items, "cursor": str(offset + take)}
    return fake_get


@pytest.fixture
def stub_get(monkeypatch):
    def apply(fn):
        monkeypatch.setattr(afterhour, "_get", fn)
        monkeypatch.setattr(afterhour.time, "sleep", lambda *_: None)
    return apply


def ids(posts):
    return [p["post"]["id"] for p in posts]


def test_fetches_every_page(stub_get):
    stub_get(make_api(total=237))
    posts = fetch_all_posts("prf_x")
    assert len(posts) == 237
    assert ids(posts) == [f"p{i}" for i in range(237)]


def test_no_duplicates_across_pages(stub_get):
    stub_get(make_api(total=500))
    posts = fetch_all_posts("prf_x")
    assert len(set(ids(posts))) == len(posts) == 500


def test_uses_a_page_size_the_gateway_survives(stub_get):
    """take=100 is accepted by the API but 504s on every cursor page — the whole
    reason a full history couldn't be fetched. Guard the size we settled on."""
    calls = []
    stub_get(make_api(total=120, calls=calls))
    fetch_all_posts("prf_x")
    assert PAGE_SIZE == 50
    assert calls[0] <= 50


def test_shrinks_page_size_when_the_gateway_times_out(stub_get):
    """A 504 that outlives the retries should halve the page and reuse the cursor,
    not abort the run."""
    calls = []
    stub_get(make_api(total=120, chokes_above=25, calls=calls))
    posts = fetch_all_posts("prf_x")
    assert len(posts) == 120
    assert ids(posts) == [f"p{i}" for i in range(120)]  # no gaps, no repeats
    assert calls[0] == PAGE_SIZE and min(calls) <= 25


def test_gives_up_only_after_shrinking_to_the_floor(stub_get):
    calls = []
    stub_get(make_api(total=120, always_504=True, calls=calls))
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch_all_posts("prf_x")
    assert excinfo.value.code == 504
    assert min(calls) == MIN_PAGE_SIZE


def test_client_errors_are_not_retried_with_smaller_pages(stub_get):
    """A 4xx means the request is wrong; shrinking it won't help."""
    calls = []

    def bad_request(url, **kwargs):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    stub_get(bad_request)
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch_all_posts("prf_x")
    assert excinfo.value.code == 404
    assert len(calls) == 1


def test_progress_callback_reports_towards_the_total(stub_get):
    stub_get(make_api(total=120))
    seen = []
    fetch_all_posts("prf_x", progress_cb=lambda n, total: seen.append((n, total)))
    assert seen[-1] == (120, 120)
    assert [n for n, _ in seen] == sorted(n for n, _ in seen)
    assert all(total == 120 for _, total in seen)


def test_empty_profile(stub_get):
    stub_get(make_api(total=0))
    assert fetch_all_posts("prf_x") == []


# ---- normalization -------------------------------------------------------

def test_normalize_extracts_the_fields_the_dashboard_uses():
    row = normalize({
        "post": {"id": "p1", "title": "Nice day", "body": "bought NVDA",
                 "createdAt": "2026-05-24T14:30:00Z", "shareUrl": "https://x/1"},
        "primaryTopicKey": "gain",
        "portfolioSnapshot": {"totalValue": 125_000},
        "securities": [{"tickerSymbol": "NVDA"}, {"tickerSymbol": "HOOD"}],
        "reactionCounts": {"like": 3, "fire": 4},
        "commentCount": 2,
        "viewCount": 99,
    })
    assert row["date"] == "2026-05-24"
    assert row["tag"] == "Gain" and row["gain_loss"] == "Gain"
    assert row["amount_k"] == 125.0
    assert row["tickers"] == "NVDA,HOOD"
    assert row["reaction_count"] == 7
    assert row["id"] == "p1"


def test_normalize_survives_a_bare_post():
    row = normalize({"post": {}})
    assert row["amount_k"] is None
    assert row["tickers"] == "" and row["tag"] == "" and row["date"] == ""


def test_normalize_falls_back_to_an_embedded_tweet():
    row = normalize({"post": {"id": "p2", "createdAt": "2026-01-01T00:00:00Z"},
                     "tweets": [{"text": "not my words", "username": "someone"}]})
    assert row["body"] == "not my words"
    assert row["embedded_tweet_from"] == "someone"


def test_normalize_only_tags_gain_or_loss_as_gain_loss():
    assert normalize({"post": {}, "primaryTopicKey": "discuss"})["gain_loss"] == ""
    assert normalize({"post": {}, "primaryTopicKey": "loss"})["gain_loss"] == "Loss"


@pytest.mark.parametrize("text", ["join my discord", "Link in bio", "lifetime access now"])
def test_funnel_keywords_match(text):
    assert FUNNEL_KEYWORDS.search(text)


@pytest.mark.parametrize("text", ["discordant views", "bought calls", ""])
def test_funnel_keywords_dont_overmatch(text):
    assert not FUNNEL_KEYWORDS.search(text)
