#!/usr/bin/env python3
"""
Regenerate the README screenshots by driving the real app with Playwright.

This is a manual tool, not part of the test suite — it needs a running app and it
hits AfterHour's live API (a full profile takes ~100s to fetch the first time).

    pip install playwright
    streamlit run streamlit_app.py --server.port 8512 --theme.base dark \
        --browser.gatherUsageStats false
    python3 scripts/capture_screenshots.py --username mphinance

Pass --chrome if Playwright can't find a browser on its own.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "screenshots"

# Union of the tab strip and everything in the visible tab panel, in page coords.
TAB_RECT = """
() => {
  const strip = document.querySelector('[role="tablist"]');
  const panel = [...document.querySelectorAll('[role="tabpanel"]')]
    .find(p => !p.hasAttribute('hidden') && p.getBoundingClientRect().height > 20);
  if (!strip || !panel) return null;
  let top=1e9, left=1e9, right=-1e9, bottom=-1e9;
  for (const e of [strip, panel, ...panel.querySelectorAll('*')]) {
    const r = e.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) continue;
    top=Math.min(top,r.top); left=Math.min(left,r.left);
    right=Math.max(right,r.right); bottom=Math.max(bottom,r.bottom);
  }
  return {x:left+window.scrollX, y:top+window.scrollY, width:right-left, height:bottom-top};
}
"""

# The raw-data tab needs bounding by hand: the dataframe's virtual scroll area is
# tens of thousands of pixels tall, so the generic union above is useless there.
DATA_RECT = """
() => {
  const els = [
    document.querySelector('[role="tablist"]'),
    document.querySelector("[data-testid='stDataFrame'], .stDataFrame"),
    [...document.querySelectorAll('button')].find(b => b.innerText.includes('Download')),
  ].filter(Boolean);
  let top=1e9, left=1e9, right=-1e9, bottom=-1e9;
  for (const e of els) {
    const r = e.getBoundingClientRect();
    if (r.height < 5) continue;
    top=Math.min(top,r.top); left=Math.min(left,r.left);
    right=Math.max(right,r.right); bottom=Math.max(bottom,r.bottom);
  }
  return {x:left+window.scrollX, y:top+window.scrollY, width:right-left, height:bottom-top};
}
"""

LANDING_RECT = """
() => {
  const h1 = document.querySelector('h1');
  const last = document.querySelector("[data-testid='stAlert']")
            || document.querySelector("[data-testid='stAlertContainer']")
            || document.querySelector("[data-testid='stForm']");
  const a = h1.getBoundingClientRect(), c = last.getBoundingClientRect();
  return {x: Math.min(a.left,c.left)+window.scrollX, y: a.top+window.scrollY,
          width: Math.max(a.right,c.right)-Math.min(a.left,c.left),
          height: c.bottom-a.top};
}
"""


def shoot(pg, name, rect, pad=16):
    if not rect:
        raise SystemExit(f"could not locate the region for {name}")
    pg.screenshot(path=OUT / name, full_page=True, clip={
        "x": max(rect["x"] - pad, 0), "y": max(rect["y"] - pad, 0),
        "width": rect["width"] + pad * 2, "height": rect["height"] + pad * 2})
    print(f"  {name}  ({rect['width']:.0f}x{rect['height']:.0f} css px)")


def wait_for_charts(pg, n, timeout=90_000):
    pg.wait_for_function(
        """(n)=>[...document.querySelectorAll("canvas, svg.marks, [data-testid='stVegaLiteChart']")]
                 .filter(e=>e.getBoundingClientRect().height>40).length >= n""",
        arg=n, timeout=timeout)
    pg.wait_for_timeout(1500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8512")
    ap.add_argument("--username", default="mphinance")
    ap.add_argument("--chrome", default=None, help="path to a Chromium binary")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    launch = {"args": ["--no-sandbox"]}
    if args.chrome:
        launch["executable_path"] = args.chrome

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        pg = browser.new_page(viewport={"width": 1440, "height": 1800}, device_scale_factor=2)
        pg.goto(args.url)
        pg.wait_for_selector("input", timeout=60_000)
        pg.wait_for_timeout(3000)  # let React attach its handlers before typing

        shoot(pg, "01-landing.png", pg.evaluate(LANDING_RECT), pad=22)

        box = pg.locator("input").first
        box.click()
        box.type(args.username, delay=40)
        pg.wait_for_timeout(400)
        pg.get_by_role("button", name="Analyze").first.click()

        print(f"  fetching @{args.username} (first run takes ~100s)...")
        for _ in range(160):
            pg.wait_for_timeout(2000)
            body = pg.inner_text("body")
            if "Total posts" in body:
                break
            if "✗" in body:
                raise SystemExit("the app reported an error: " + body[body.index("✗"):][:200])
        else:
            raise SystemExit("timed out waiting for the dashboard")
        wait_for_charts(pg, 1)

        head = pg.locator("h3").filter(has_text=args.username).first
        metrics = pg.locator("[data-testid='stMetric']")
        hb = head.bounding_box()
        lb = metrics.nth(metrics.count() - 1).bounding_box()
        shoot(pg, "02-overview.png", {
            "x": hb["x"], "y": hb["y"],
            "width": max(hb["width"], lb["width"] * 3.2),
            "height": (lb["y"] + lb["height"]) - hb["y"]})

        for name, label, charts in [("03-activity.png", "Activity", 2),
                                    ("04-tickers.png", "Tickers", 1),
                                    ("05-portfolio.png", "Portfolio value", 1),
                                    ("06-data.png", "Raw data", 0)]:
            pg.get_by_role("tab", name=label).first.click()
            pg.wait_for_timeout(1500)
            if charts:
                wait_for_charts(pg, charts)
            else:
                pg.wait_for_selector("[data-testid='stDataFrame'], .stDataFrame", timeout=60_000)
                pg.wait_for_timeout(2000)
            shoot(pg, name, pg.evaluate(DATA_RECT if charts == 0 else TAB_RECT))

        browser.close()
    print(f"done -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
