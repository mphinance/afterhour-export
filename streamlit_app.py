"""
AfterHour Post Analyzer — put in a username, get your whole trading-post history
back as charts, tables, and a downloadable CSV.

No login. No API key. Public AfterHour data only, fetched live when you hit Analyze.
"""
from __future__ import annotations

import csv
import io
import urllib.error
from collections import Counter

import altair as alt
import pandas as pd
import streamlit as st

from afterhour import FUNNEL_KEYWORDS, fetch_all_posts, normalize, profile_id

st.set_page_config(page_title="AfterHour Post Analyzer", page_icon="📊", layout="wide")


@st.cache_data(ttl=3600, show_spinner=False)
def load_profile(username: str) -> pd.DataFrame:
    # The progress widgets have to be created in here, not passed in. Streamlit
    # replays the element calls a cached function made, and replaying a write to
    # a placeholder created outside the function is an error on every cache hit.
    # Both are cleared before returning, so a replay draws nothing.
    status = st.empty()
    bar = st.empty()

    def progress(n: int, total: int) -> None:
        status.write(f"Fetching posts... {n}/{total}")
        bar.progress(min(n / total, 1.0) if total else 0.0)

    author_id = profile_id(username)
    raw = fetch_all_posts(author_id, progress_cb=progress)
    status.empty()
    bar.empty()
    rows = [normalize(item) for item in raw]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("created_at", ascending=False).reset_index(drop=True)
    return df


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

st.title("📊 AfterHour Post Analyzer")
st.caption(
    "Put in any AfterHour username — yours or anyone else's public profile — and get "
    "their full post history back as charts and a downloadable spreadsheet. Nothing "
    "here needs a login; it's all pulled live from AfterHour's own public feed."
)

with st.form("lookup"):
    col1, col2 = st.columns([4, 1])
    username = col1.text_input("AfterHour username", placeholder="e.g. mphinance", label_visibility="collapsed")
    submitted = col2.form_submit_button("Analyze →", use_container_width=True)

if submitted and not username.strip():
    st.warning("Type a username first.")
    st.stop()

if not submitted and "last_username" not in st.session_state:
    st.info("👆 Enter a username above to get started. Try your own, or a friend's.")
    st.stop()

target = username.strip() if submitted else st.session_state.get("last_username", "")
if submitted:
    st.session_state["last_username"] = target

try:
    with st.spinner(f"Looking up @{target}..."):
        df = load_profile(target)
except LookupError as e:
    st.error(f"✗ {e}")
    st.stop()
except urllib.error.HTTPError as e:
    if e.code >= 500:
        st.error(
            f"✗ AfterHour's API is timing out on us (HTTP {e.code}: {e.reason}). That's "
            f"on their end, not yours, and it's usually brief — give it a minute and "
            f"hit Analyze again."
        )
    else:
        st.error(f"✗ AfterHour's API returned HTTP {e.code}: {e.reason}")
    st.stop()
except Exception as e:
    st.error(f"✗ Something went wrong talking to AfterHour: {e}")
    st.stop()

if df.empty:
    st.warning(f"@{target} has no public posts.")
    st.stop()

# ---- header stats ----------------------------------------------------
n_posts = len(df)
date_start, date_end = df["date"].min(), df["date"].max()
days_active = max((date_end - date_start).days, 1)
posts_per_week = n_posts / (days_active / 7)
total_engagement = int(df["reaction_count"].sum() + df["comment_count"].sum())

st.subheader(f"@{target}")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Total posts", f"{n_posts:,}")
m2.metric("Active since", date_start.strftime("%b %Y"))
m3.metric("Posts / week", f"{posts_per_week:.1f}")
m4.metric("Total reactions + comments", f"{total_engagement:,}")

gains = int((df["tag"] == "Gain").sum())
losses = int((df["tag"] == "Loss").sum())
funnel_hits = int(df.apply(lambda r: bool(FUNNEL_KEYWORDS.search(f"{r['title']} {r['body']}")), axis=1).sum())
hunt_pct = 100 * df["is_hunt_post"].mean()

c1, c2, c3 = st.columns(3)
c1.metric("Self-tagged Gain : Loss", f"{gains} : {losses}")
c2.metric("Posts mentioning a paid product/Discord/etc.", f"{funnel_hits} ({100*funnel_hits/n_posts:.0f}%)")
c3.metric("Embedded-tweet posts (not their own words)", f"{hunt_pct:.0f}%")

st.divider()

tab_activity, tab_tags, tab_tickers, tab_portfolio, tab_data = st.tabs(
    ["📅 Activity", "🏷️ Tags", "💹 Tickers", "💰 Portfolio value", "📄 Raw data"]
)

with tab_activity:
    weekly = df.set_index("created_at").resample("W", label="left").size().rename("posts").reset_index()
    chart = alt.Chart(weekly).mark_area(opacity=0.5, interpolate="monotone").encode(
        x=alt.X("created_at:T", title=None),
        y=alt.Y("posts:Q", title="posts / week"),
        tooltip=["created_at:T", "posts:Q"],
    ).properties(height=280)
    st.altair_chart(chart, use_container_width=True)

    st.caption("When they actually post — hour of day (their post's UTC hour) × day of week")
    heat = df.copy()
    heat["hour"] = heat["created_at"].dt.hour
    heat["weekday"] = heat["created_at"].dt.day_name()
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    heat_counts = heat.groupby(["weekday", "hour"]).size().rename("count").reset_index()
    heat_chart = alt.Chart(heat_counts).mark_rect().encode(
        x=alt.X("hour:O", title="hour (UTC)"),
        y=alt.Y("weekday:O", title=None, sort=weekday_order),
        color=alt.Color("count:Q", scale=alt.Scale(scheme="oranges"), legend=None),
        tooltip=["weekday", "hour", "count"],
    ).properties(height=220)
    st.altair_chart(heat_chart, use_container_width=True)

with tab_tags:
    tag_counts = df[df["tag"] != ""]["tag"].value_counts().rename_axis("tag").reset_index(name="count")
    if not tag_counts.empty:
        chart = alt.Chart(tag_counts).mark_bar().encode(
            x=alt.X("count:Q"),
            y=alt.Y("tag:N", sort="-x"),
            color=alt.Color("tag:N", legend=None),
            tooltip=["tag", "count"],
        ).properties(height=32 * len(tag_counts) + 40)
        st.altair_chart(chart, use_container_width=True)
    else:
        st.caption("No self-tagged posts (Gain/Loss/Discuss/etc.) to show.")

with tab_tickers:
    all_tickers = Counter(t for row in df["tickers"] if row for t in row.split(",") if t)
    if all_tickers:
        tk = pd.DataFrame(all_tickers.most_common(20), columns=["ticker", "mentions"])
        chart = alt.Chart(tk).mark_bar().encode(
            x=alt.X("mentions:Q"),
            y=alt.Y("ticker:N", sort="-x"),
            tooltip=["ticker", "mentions"],
        ).properties(height=28 * len(tk) + 40)
        st.altair_chart(chart, use_container_width=True)
    else:
        st.caption("No ticker mentions found in this profile's posts.")

with tab_portfolio:
    st.warning(
        "⚠️ **Not a gain/loss number.** This is the account's *total* value (cash + "
        "positions) at the moment of each post — and AfterHour's own brokerage sync "
        "has been known to glitch and show wrong figures. Treat sudden spikes/drops "
        "as suspect unless the post's own text explains them.",
        icon="⚠️",
    )
    pv = df.dropna(subset=["amount_k"])[["created_at", "amount_k"]].sort_values("created_at")
    if not pv.empty:
        chart = alt.Chart(pv).mark_line(point=True, interpolate="monotone").encode(
            x=alt.X("created_at:T", title=None),
            y=alt.Y("amount_k:Q", title="portfolio value ($K)"),
            tooltip=["created_at:T", "amount_k:Q"],
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)
    else:
        st.caption("No portfolio value data on these posts.")

with tab_data:
    display_cols = ["date", "tag", "gain_loss", "amount_k", "tickers", "title",
                     "comment_count", "reaction_count", "view_count", "is_hunt_post"]
    st.dataframe(df[display_cols], use_container_width=True, height=420)

    export_cols = ["date", "tag", "gain_loss", "amount_k", "tickers", "title", "body",
                    "embedded_tweet_from", "is_hunt_post", "comment_count",
                    "reaction_count", "view_count", "id", "share_url", "link_urls"]
    buf = io.StringIO()
    df[export_cols].to_csv(buf, index=False, quoting=csv.QUOTE_MINIMAL)
    st.download_button(
        "⬇ Download full CSV",
        data=buf.getvalue(),
        file_name=f"{target}.csv",
        mime="text/csv",
    )

st.divider()
st.caption(
    "Built on AfterHour's public, unauthenticated feed API — no login used, nothing "
    "stored server-side beyond a 1-hour cache. "
    "[Standalone script version](https://gist.github.com/mphinance/8be410783fe65efe3894198f88388d2a) · "
    "[source](https://github.com/mphinance/afterhour-export)"
)
