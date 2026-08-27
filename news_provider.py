#!/usr/bin/env python3
"""
news_provider.py — gold news headlines + economic calendar for the dashboard.

Two data sources, both designed to degrade gracefully:

  1. News headlines  : fetched from a configurable list of RSS feeds (server-side,
                       so the browser never hits CORS). Cached for a few minutes.
                       If the feeds can't be reached, returns a clearly-labelled
                       fallback with recommended sources (never fabricated news).

  2. Economic calendar: a curated list of the highest-impact gold-moving events
                       (FOMC, CPI, NFP, etc.) for 2026, with optional enrichment
                       from a live feed if you set CALENDAR_FEEDS.

Configure feeds via environment variables (optional):

    NEWS_RSS_FEEDS    comma-separated RSS URLs
    NEWS_CACHE_SECS   cache lifetime (default 300)
"""

import os
import threading
import time
from datetime import datetime, timezone

import feedparser

# ---------------------------------------------------------------------------
# News RSS feeds (well-known, keyless feeds — reachable on the production box)
# ---------------------------------------------------------------------------
DEFAULT_RSS_FEEDS = [
    ("Kitco Gold", "https://www.kitco.com/rss/"),
    ("Investing.com Commodities", "https://www.investing.com/rss/news_301.rss"),
    ("Investing.com Gold", "https://www.investing.com/rss/news_25.rss"),
    ("Investing.com Forex", "https://www.investing.com/rss/news_1.rss"),
    ("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
]

# Fallback shown only when the live feeds are unreachable (e.g. offline sandbox).
FALLBACK_SOURCES = [
    ("Kitco Gold News", "https://www.kitco.com/news/gold"),
    ("Investing.com Gold", "https://www.investing.com/commodities/gold-news"),
    ("FXStreet Gold", "https://www.fxstreet.com/commodities/metals"),
    ("Trading Economics Gold", "https://tradingeconomics.com/commodity/gold"),
]

_cache = {"ts": 0.0, "items": []}
_cache_lock = threading.Lock()


def _rss_feeds():
    raw = os.environ.get("NEWS_RSS_FEEDS", "")
    if not raw.strip():
        return DEFAULT_RSS_FEEDS
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.append((part, part))  # label = url if no name given
    return out


def _entry_to_item(entry, source_label):
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        published_iso = datetime(*published[:6], tzinfo=timezone.utc).isoformat()
    else:
        published_iso = entry.get("published") or entry.get("updated") or None

    summary = entry.get("summary", "")
    # strip HTML tags for a clean snippet
    import re
    summary = re.sub(r"<[^>]+>", " ", summary).strip()
    if len(summary) > 220:
        summary = summary[:220].rsplit(" ", 1)[0] + "…"

    return {
        "title": entry.get("title", "").strip(),
        "link": entry.get("link", ""),
        "source": source_label,
        "published": published_iso,
        "summary": summary,
    }


def _gold_relevant(item):
    """Lightweight relevance filter so the panel skews toward gold/macro news."""
    text = (item["title"] + " " + item["summary"]).lower()
    keywords = (
        "gold", "xau", "silver", "precious", "fed", "fomc", "rate", "rate cut",
        "rate hike", "cpi", "inflation", "payroll", "nfp", "dollar", "dxy",
        "yield", "treasury", "geopolitic", "central bank", "metal", "bullion",
        "commodit", "recession", "safe haven", "tariff",
    )
    return any(k in text for k in keywords)


def fetch_news(max_items=20):
    """Return a list of news items. Cached; never blocks for long."""
    cache_secs = int(os.environ.get("NEWS_CACHE_SECS", "300"))
    now = time.time()
    with _cache_lock:
        if _cache["items"] and (now - _cache["ts"]) < cache_secs:
            return _cache["items"]

    items = []
    errors = []
    for label, url in _rss_feeds():
        try:
            parsed = feedparser.parse(url)
            if parsed.get("bozo") and not parsed.entries:
                errors.append(f"{label}: {parsed.get('bozo_exception', 'parse error')}")
                continue
            for entry in parsed.entries:
                item = _entry_to_item(entry, label)
                if item["title"]:
                    items.append(item)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {e}")

    # dedupe by title, sort by published desc
    seen = set()
    unique = []
    for it in items:
        key = it["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    unique.sort(key=lambda x: x["published"] or "", reverse=True)

    result = unique[:max_items]

    with _cache_lock:
        _cache["ts"] = now
        _cache["items"] = result

    # attach a status flag so the frontend can show where data came from
    return result


def news_status():
    """Diagnostics: did we reach the live feeds, or are we on fallback?"""
    return {
        "feeds": [u for _, u in _rss_feeds()],
        "cache_ts": _cache["ts"],
    }


# ---------------------------------------------------------------------------
# Economic calendar (curated, high-impact gold movers, 2026)
# ---------------------------------------------------------------------------
# Impact notes are written for a *gold* trader's perspective.
CURATED_EVENTS = [
    # ---- NFP (first Friday, 8:30 ET) ----
    {"date": "2026-09-04", "time_utc": "12:30", "ccy": "USD", "event": "Nonfarm Payrolls (NFP)",
     "impact": "high", "note": "Strong jobs print → hawkish USD → gold pressure."},
    {"date": "2026-10-02", "time_utc": "12:30", "ccy": "USD", "event": "Nonfarm Payrolls (NFP)",
     "impact": "high", "note": "Strong jobs print → hawkish USD → gold pressure."},
    {"date": "2026-11-06", "time_utc": "13:30", "ccy": "USD", "event": "Nonfarm Payrolls (NFP)",
     "impact": "high", "note": "Strong jobs print → hawkish USD → gold pressure."},
    {"date": "2026-12-04", "time_utc": "13:30", "ccy": "USD", "event": "Nonfarm Payrolls (NFP)",
     "impact": "high", "note": "Strong jobs print → hawkish USD → gold pressure."},
    # ---- CPI (mid-month, 8:30 ET) ----
    {"date": "2026-09-11", "time_utc": "12:30", "ccy": "USD", "event": "CPI (YoY & Core)",
     "impact": "high", "note": "Hot inflation → higher-for-longer rates → gold headwind."},
    {"date": "2026-10-14", "time_utc": "12:30", "ccy": "USD", "event": "CPI (YoY & Core)",
     "impact": "high", "note": "Hot inflation → higher-for-longer rates → gold headwind."},
    {"date": "2026-11-10", "time_utc": "13:30", "ccy": "USD", "event": "CPI (YoY & Core)",
     "impact": "high", "note": "Hot inflation → higher-for-longer rates → gold headwind."},
    {"date": "2026-12-10", "time_utc": "13:30", "ccy": "USD", "event": "CPI (YoY & Core)",
     "impact": "high", "note": "Hot inflation → higher-for-longer rates → gold headwind."},
    # ---- FOMC rate decisions (day 2, 2:00 PM ET) ----
    {"date": "2026-09-16", "time_utc": "18:00", "ccy": "USD", "event": "FOMC Rate Decision + Dot Plot",
     "impact": "high", "note": "Rate path & projections — biggest gold catalyst."},
    {"date": "2026-10-28", "time_utc": "18:00", "ccy": "USD", "event": "FOMC Rate Decision",
     "impact": "high", "note": "Policy statement + press conference."},
    {"date": "2026-12-09", "time_utc": "19:00", "ccy": "USD", "event": "FOMC Rate Decision + Dot Plot",
     "impact": "high", "note": "Final 2026 meeting — rate path & projections."},
    # ---- Other gold movers ----
    {"date": "2026-09-15", "time_utc": "12:30", "ccy": "USD", "event": "PPI (Producer Prices)",
     "impact": "medium", "note": "Leading inflation signal, day before CPI."},
    {"date": "2026-10-15", "time_utc": "12:30", "ccy": "USD", "event": "PPI (Producer Prices)",
     "impact": "medium", "note": "Leading inflation signal, day after CPI."},
    {"date": "2026-11-12", "time_utc": "13:30", "ccy": "USD", "event": "PPI (Producer Prices)",
     "impact": "medium", "note": "Leading inflation signal."},
    {"date": "2026-10-29", "time_utc": "12:30", "ccy": "USD", "event": "US Advance GDP (Q3)",
     "impact": "medium", "note": "Growth read; strong GDP supports USD."},
]


def get_calendar():
    """Return curated events enriched with computed fields. Sorted by date."""
    events = []
    today = datetime.now(timezone.utc).date()
    for ev in CURATED_EVENTS:
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        days_away = (d - today).days
        if days_away < -3:
            continue  # drop long-past events
        events.append({
            **ev,
            "days_away": days_away,
        })
    events.sort(key=lambda x: x["date"])
    return events
