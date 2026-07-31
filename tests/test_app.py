"""
End-to-end tests for the Streamlit app itself, driven by Streamlit's AppTest.

The feed client is stubbed, so these never touch the network. They exist mainly
to cover the cache-replay trap: a cached function that writes to widgets created
outside itself works on the first call and then throws on every cache hit, which
is invisible to a single-run test.
"""
from __future__ import annotations

import pathlib

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import afterhour

APP = str(pathlib.Path(__file__).resolve().parents[1] / "streamlit_app.py")


def fake_items(n=40):
    return [
        {
            "post": {"id": f"p{i}", "title": f"post {i}", "body": "join my discord" if i % 10 == 0 else "bought calls",
                     "createdAt": f"2026-0{1 + i % 9}-1{i % 9}T12:00:00Z", "shareUrl": f"https://x/{i}"},
            "primaryTopicKey": "gain" if i % 3 else "loss",
            "portfolioSnapshot": {"totalValue": 100_000 + i * 100},
            "securities": [{"tickerSymbol": "NVDA" if i % 2 else "HOOD"}],
            "reactionCounts": {"like": i}, "commentCount": i, "viewCount": i * 3,
        }
        for i in range(n)
    ]


@pytest.fixture
def stubbed_feed(monkeypatch):
    """Serve canned posts, and record that the progress callback actually ran."""
    progress_calls = []

    def fetch(author_id, progress_cb=None):
        items = fake_items()
        if progress_cb:
            progress_cb(len(items), len(items))
            progress_calls.append(len(items))
        return items

    monkeypatch.setattr(afterhour, "profile_id", lambda username: "prf_fake")
    monkeypatch.setattr(afterhour, "fetch_all_posts", fetch)
    st.cache_data.clear()
    yield progress_calls
    st.cache_data.clear()


def analyze(username="mphinance"):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.text_input[0].set_value(username)
    at.button[0].click().run()
    return at


def test_analyzing_a_profile_renders_the_dashboard(stubbed_feed):
    at = analyze()
    assert not at.exception
    assert not at.error
    labels = [m.label for m in at.metric]
    assert "Total posts" in labels
    assert any(t.label.endswith("Activity") for t in at.tabs)


def test_progress_runs_during_the_fetch(stubbed_feed):
    analyze()
    assert stubbed_feed == [40], "the progress callback should be driven by the fetch"


def test_a_cached_reanalysis_does_not_error(stubbed_feed):
    """The regression: the second lookup of a username is served from cache, and
    Streamlit replays whatever elements the cached function drew. If the progress
    widgets live outside it, that replay fails and the user sees an error instead
    of their dashboard."""
    first = analyze()
    assert not first.error

    second = analyze()
    assert not second.exception
    assert not second.error, f"cached re-run surfaced an error: {[e.value for e in second.error]}"
    assert "Total posts" in [m.label for m in second.metric]


def test_blank_username_is_rejected(stubbed_feed):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.text_input[0].set_value("   ")
    at.button[0].click().run()
    assert not at.exception
    assert any("Type a username" in w.value for w in at.warning)


def test_unknown_username_shows_a_readable_error(stubbed_feed, monkeypatch):
    def missing(username):
        raise LookupError(f"'{username}' doesn't seem to exist on AfterHour.")

    monkeypatch.setattr(afterhour, "profile_id", missing)
    at = analyze("nope")
    assert not at.exception
    assert any("doesn't seem to exist" in e.value for e in at.error)
