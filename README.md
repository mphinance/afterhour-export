# AfterHour Post Analyzer

Type in an AfterHour username, get their whole post history back as charts and a
downloadable CSV. No login, no API key — AfterHour's post feed is public.

**Live app:** deploy this repo on [Streamlit Community Cloud](https://streamlit.io/cloud)
pointed at `streamlit_app.py`, or run it yourself:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## What it shows

- Posting activity over time, plus a day/hour heatmap of when they actually post
- Tag breakdown (Gain/Loss/Discuss/DD/etc. — self-selected by the poster, not verified)
- Most-mentioned tickers
- Portfolio value over time (**flagged as unreliable** — AfterHour's brokerage sync has
  been known to glitch; see the warning in the app itself)
- How many posts mention a paid product/Discord/Substack/etc.
- Full raw data table + CSV export

## Just want the data, not the dashboard?

Standalone script, no Streamlit needed:
https://gist.github.com/mphinance/8be410783fe65efe3894198f88388d2a

```bash
python3 fetch_my_afterhour_posts.py --username YourHandle
```

## How the data source works

`afterhour.com/<username>`'s post feed is powered by a public, unauthenticated API:

```
https://api.afterhour.com/social/feed?take=100&contentTypes=post&authorId=<prf_id>
```

`take` is capped at 100 per request — page through with the response's `cursor` field
to get everything. This app and the standalone script both handle that automatically.
