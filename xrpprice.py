#!/usr/bin/env python3
"""
XRP price and news monitor.

Price dashboard: one Discord message, edited in place.
News dashboard: one separate Discord message, edited in place.
News history: persisted to xrp_state.json between GitHub Actions runs.
News dashboard includes an attached sentiment timeline and topic breakdown chart.
"""

import json
import os
import re
import sys
import time
import hashlib
import html
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse
from io import BytesIO

import requests
from bs4 import BeautifulSoup

# Matplotlib is used only for the news dashboard image. The price dashboard
# remains a normal Discord embed and does not depend on chart generation.
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter
    CHARTS_AVAILABLE = True
except Exception as chart_import_error:
    plt = None
    DateFormatter = None
    CHARTS_AVAILABLE = False

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

BUY_LEVEL_ZAR = 21.50
ALERT_DIRECTION = "above"       # "above" or "below"
PCT_MOVE_ALERT = 3.0            # % move over roughly one hour; 0 disables
CMC_EVERY_MINUTES = 10
NEWS_CHECK_EVERY = 5            # minutes
RUN_MINUTES = 210               # 3.5 hours; workflow starts every 4 hours
NEWS_HISTORY_HOURS = 48
MAX_NEWS_ITEMS = 100
MAX_DISPLAY_NEWS = 6
MAX_ALERTS_PER_SCAN = 5

SEND_MIXED = True
SEND_CRITICAL_NEWS_ALERTS = True  # critical news also gets one separate alert
STATE_FILE = os.getenv("XRP_STATE_FILE", "xrp_state.json")

CMC_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
CMC_XRP_ID = "52"

NEWS_FEEDS = {
    "SEC": {
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "mode": "sec",
    },
    "Ripple": {
        "url": "https://news.google.com/rss/search?q=site:ripple.com+when:7d&hl=en-US&gl=US&ceid=US:en",
        "mode": "ripple",
    },
    "XRP News": {
        "url": "https://news.google.com/rss/search?q=XRP+OR+%22Ripple+Labs%22+when:1d&hl=en-US&gl=US&ceid=US:en",
        "mode": "news",
    },
    "XRP ETF": {
        "url": "https://news.google.com/rss/search?q=XRP+ETF+inflows+OR+approval+when:3d&hl=en-US&gl=US&ceid=US:en",
        "mode": "news",
    },
    "Ripple Deals": {
        "url": "https://news.google.com/rss/search?q=Ripple+partnership+OR+RLUSD+OR+%22Ripple+Prime%22+OR+licence+when:3d&hl=en-US&gl=US&ceid=US:en",
        "mode": "news",
    },
}

# Trust is based on the actual publisher/source or resolved article hostname,
# not merely a word appearing in the headline.
TRUSTED_SOURCES = {
    "reuters.com", "bloomberg.com", "coindesk.com", "theblock.co",
    "cointelegraph.com", "decrypt.co", "cnbc.com", "wsj.com",
    "ft.com", "forbes.com", "fortune.com", "finance.yahoo.com",
    "marketwatch.com", "barrons.com", "ripple.com", "sec.gov",
    "securities.io",
}
TRUSTED_SOURCE_NAMES = {
    "reuters", "bloomberg", "coindesk", "the block", "cointelegraph",
    "decrypt", "cnbc", "wall street journal", "financial times",
    "forbes", "fortune", "yahoo finance", "marketwatch", "barron's",
    "barrons", "ripple", "u.s. securities and exchange commission",
    "sec",
}

BLOCK_WORDS = [
    "price prediction", "price forecast", "predicts", "prediction",
    "could hit", "will hit", "to the moon", "giveaway", "airdrop",
    "presale", "pre-sale", "100x", "1000x", "next bitcoin",
    "best crypto to buy", "top 5", "top 10", "meme coin", "memecoin",
    "buy now", "millionaire", "sponsored", "advertorial", "get rich",
    "passive income", "price analysis", "live price", "price chart",
    "converter", "price today",
]

PRIORITY_WORDS = [
    "approved", "approval", "lawsuit", "ruling", "court", "etf",
    "settlement", "license", "licence", "acquisition", "acquires",
    "partnership", "delist", "hack", "breach", "injunction",
    "appeal", "dismiss", "charges", "launch",
]

BULLISH = {
    "net inflow": 3, "net inflows": 3, "inflows": 2,
    "etf approved": 3, "etf approval": 3,
    "approves": 2, "approved": 2, "approval": 1,
    "drops lawsuit": 3, "dismissed": 2, "dismisses": 2,
    "drops appeal": 3, "wins": 2, "settles": 1,
    "partnership": 1, "partners with": 1, "adopts": 2,
    "adoption": 1, "integrates": 1,
    "licence granted": 3, "license granted": 3,
    "granted a licence": 3, "granted a license": 3,
    "regulatory clarity": 2, "launches": 1, "launch": 1,
    "acquires": 1, "acquisition": 1, "record high": 2,
    "all-time high": 2, "surge": 1, "rallies": 1,
    "bullish": 1, "institutional demand": 2,
    "institutional adoption": 2,
}

BEARISH = {
    "net outflow": 3, "net outflows": 3, "outflows": 2,
    "rejected": 2, "rejects": 2, "delays": 2, "delayed": 2,
    "hack": 3, "hacked": 3, "breach": 3, "exploit": 3,
    "sues": 2, "sued": 2, "lawsuit filed": 2,
    "fraud": 3, "investigation": 2, "fined": 2, "fine": 1,
    "charges": 2, "ban": 2, "bans": 2, "crash": 2,
    "plunge": 2, "plunges": 2, "sell-off": 1, "selloff": 1,
    "dump": 1, "delist": 3, "bearish": 1, "warns": 1,
    "concern": 1, "uncertainty": 1, "withdraws": 2,
    "withdrawn": 2, "suspends": 2, "suspended": 2,
}

# These are deliberately stricter than simply seeing "XRP ETF".
DIRECT_DEMAND_PATTERNS = [
    r"\b(etf|funds?)\b.{0,45}\b(net )?inflows?\b",
    r"\b(inflows?)\b.{0,45}\b(xrp|etf)\b",
    r"\b(buys?|bought|purchases?|purchased|accumulates?)\b.{0,45}\bxrp\b",
    r"\bxrp\b.{0,45}\b(buys?|bought|purchases?|purchased|accumulates?)\b",
    r"\b(xrp payments?|xrp-based payments?|on-demand liquidity|odl)\b",
    r"\buses? xrp\b|\busing xrp\b|\bxrp as (a )?bridge\b",
    r"\b(xrp treasury|xrp reserve|xrp holdings)\b",
    r"\badds? xrp\b.{0,35}\b(reserves?|holdings?|treasury)\b",
]
DIRECT_DEMAND_RE = [re.compile(p, re.I) for p in DIRECT_DEMAND_PATTERNS]

RELEVANT_RE = re.compile(r"\bxrp\b|\bripple\b|garlinghouse|rlusd", re.I)
NEGATION_RE = re.compile(
    r"\b(no|not|never|without|fails? to|failed to|denies?|denied|"
    r"rejects?|rejected|halts?|halted|slows?|slowed|weak|lower|declines?)\b",
    re.I,
)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; XRPMonitor/2.0)"}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

WEBHOOK = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
CMC_KEY = (os.getenv("CMC_API_KEY") or "").strip()
DISCORD_USER_ID = (os.getenv("DISCORD_USER_ID") or "").strip()


# ---------------------------------------------------------------------------
# STATE AND UTILITIES
# ---------------------------------------------------------------------------

def log(*args):
    print(*args, flush=True)


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat(timespec="seconds")


def empty_state():
    return {
        "version": 2,
        "news_message_id": None,
        "news_history": [],
        "seen_links": {},
        "seen_titles": [],
        "initialized": {},
        "urgent_sent": [],
        "last_news_render": "",
        "fx_rate": None,
        "fx_rate_time": None,
    }


def load_state():
    state = empty_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state.update(saved)
    except FileNotFoundError:
        log("No prior state file; starting a new state.")
    except Exception as exc:
        log(f"State file could not be read; starting safely: {exc}")

    # Keep expected types valid if a partial/old state file is found.
    for key, default in empty_state().items():
        # None defaults are optional fields (e.g. Discord message ID and FX rate).
        # Accept their later non-None values when loading persisted state.
        if default is None:
            continue
        if not isinstance(state.get(key), type(default)):
            state[key] = default
    return state


def save_state(state):
    """Atomic local save. The workflow commits this file after the run."""
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        log(f"WARNING: state save failed: {exc}")


def prune_state(state):
    cutoff = time.time() - NEWS_HISTORY_HOURS * 3600
    state["news_history"] = [
        item for item in state["news_history"]
        if isinstance(item, dict) and float(item.get("ts", 0)) >= cutoff
    ][-MAX_NEWS_ITEMS:]

    state["urgent_sent"] = [
        item for item in state["urgent_sent"]
        if isinstance(item, dict) and float(item.get("ts", 0)) >= cutoff
    ][-100:]

    # Bound URL/title memory while retaining recent dedupe protection.
    for feed, links in list(state["seen_links"].items()):
        if isinstance(links, list):
            state["seen_links"][feed] = links[-250:]
        else:
            state["seen_links"][feed] = []
    state["seen_titles"] = state["seen_titles"][-500:]


def short_num(number, prefix=""):
    number = float(number)
    for limit, suffix in (
        (1e15, "Q"), (1e12, "T"), (1e9, "B"),
        (1e6, "M"), (1e3, "K"),
    ):
        if abs(number) >= limit:
            return f"{prefix}{number / limit:,.2f}{suffix}"
    return f"{prefix}{number:,.2f}"


def safe_float(value):
    try:
        result = float(value)
        return result if result == result and abs(result) != float("inf") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# PRICE DATA
# ---------------------------------------------------------------------------

def get_usd_to_zar(state):
    try:
        response = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            headers=HEADERS, timeout=10
        )
        response.raise_for_status()
        rate = safe_float(response.json().get("rates", {}).get("ZAR"))
        if rate and rate > 0:
            state["fx_rate"] = rate
            state["fx_rate_time"] = time.time()
            return rate
    except Exception as exc:
        log(f"Fiat fetch failed: {exc}")

    cached = safe_float(state.get("fx_rate"))
    if cached and cached > 0:
        age = time.time() - float(state.get("fx_rate_time") or 0)
        if age <= 24 * 3600:
            log(f"Using cached USD/ZAR rate ({age / 3600:.1f}h old).")
            return cached
    raise RuntimeError("No current or recent USD/ZAR exchange rate available.")


def fetch_cmc(state):
    response = requests.get(
        CMC_URL,
        headers={"X-CMC_PRO_API_KEY": CMC_KEY, "Accept": "application/json"},
        params={"id": CMC_XRP_ID, "convert": "ZAR"},
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"CoinMarketCap HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()["data"][CMC_XRP_ID]
    if isinstance(data, list):
        data = data[0]
    quote = data["quote"]["ZAR"]
    price_zar = float(quote["price"])
    market_cap_zar = float(quote.get("market_cap") or 0)
    if not market_cap_zar and data.get("circulating_supply"):
        market_cap_zar = price_zar * float(data["circulating_supply"])
    rate = get_usd_to_zar(state)
    return {
        "price_zar": price_zar,
        "mcap_zar": market_cap_zar,
        "price_usd": price_zar / rate,
        "mcap_usd": market_cap_zar / rate,
        "usd_to_zar": rate,
        "source": "CoinMarketCap",
    }


def fetch_coinlore(state):
    response = requests.get(
        "https://api.coinlore.net/api/ticker/?id=58",
        headers=HEADERS, timeout=10
    )
    response.raise_for_status()
    coin = response.json()[0]
    price_usd = float(coin["price_usd"])
    market_cap_usd = float(coin["market_cap_usd"])
    rate = get_usd_to_zar(state)
    return {
        "price_usd": price_usd,
        "price_zar": price_usd * rate,
        "mcap_usd": market_cap_usd,
        "mcap_zar": market_cap_usd * rate,
        "usd_to_zar": rate,
        "source": "CoinLore (backup)",
    }


def get_prices(state, price_state):
    now = time.time()
    if CMC_KEY and now >= price_state["next_cmc"]:
        try:
            data = fetch_cmc(state)
            data["time"] = now
            price_state["cmc"] = data
            price_state["next_cmc"] = now + CMC_EVERY_MINUTES * 60
            log(f"CoinMarketCap OK: R{data['price_zar']:.2f}")
            return data, True
        except Exception as exc:
            log(f"CoinMarketCap fetch failed: {exc}")
            price_state["next_cmc"] = now + 120

    cached = price_state.get("cmc")
    if cached and now - cached["time"] < 30 * 60:
        return cached, False

    try:
        data = fetch_coinlore(state)
        data["time"] = now
        return data, True
    except Exception as exc:
        log(f"CoinLore fetch failed: {exc}")
        return None, False


def build_price_payload(data):
    updated = time.strftime("%H:%M:%S", time.gmtime(data["time"]))
    return {
        "username": "XRP Live Dashboard",
        "embeds": [{
            "title": "⚡ Live XRP Market Ticker",
            "color": 5763719,
            "fields": [
                {"name": "💵 Price (USD)", "value": f"${data['price_usd']:,.4f}", "inline": True},
                {"name": "🇿🇦 Price (ZAR)", "value": f"R {data['price_zar']:,.2f}", "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": False},
                {"name": "📈 Market Cap (USD)", "value": short_num(data["mcap_usd"], "$"), "inline": True},
                {"name": "🇿🇦 Market Cap (ZAR)", "value": short_num(data["mcap_zar"], "R "), "inline": True},
            ],
            "footer": {
                "text": f"{data['source']} · price as of {updated} UTC · level R {BUY_LEVEL_ZAR:.2f}"
            },
            "timestamp": iso_now(),
        }],
    }


# ---------------------------------------------------------------------------
# DISCORD
# ---------------------------------------------------------------------------

def send_alert(message):
    if not WEBHOOK:
        return
    mention = f"<@{DISCORD_USER_ID}> " if DISCORD_USER_ID else ""
    payload = {
        "username": "XRP Alerts",
        "content": f"{mention}{message}"[:1990],
        "allowed_mentions": {"users": [DISCORD_USER_ID]} if DISCORD_USER_ID else {"parse": []},
    }
    try:
        response = requests.post(WEBHOOK, json=payload, timeout=15)
        if response.status_code not in (200, 204):
            log("Alert post failed:", response.status_code, response.text[:300])
    except Exception as exc:
        log(f"Alert error: {exc}")


def create_or_edit_webhook_message(message_id, payload, files=None):
    """Create once, then edit the same webhook message, optionally replacing chart files."""
    request_kwargs = {"timeout": 30}
    if files:
        # Discord expects multipart form data when an embed references
        # attachment:// filenames. IDs 0..N identify the new uploads.
        payload = dict(payload)
        payload["attachments"] = [
            {"id": index, "filename": filename}
            for index, (filename, content, content_type) in enumerate(files)
        ]
        request_kwargs["data"] = {
            "payload_json": json.dumps(payload, ensure_ascii=False)
        }
        request_kwargs["files"] = [
            (f"files[{index}]", (filename, content, content_type))
            for index, (filename, content, content_type) in enumerate(files)
        ]
    else:
        request_kwargs["json"] = payload

    if message_id:
        response = requests.patch(
            f"{WEBHOOK}/messages/{message_id}", **request_kwargs
        )
        if response.status_code in (200, 204):
            return message_id
        log(f"Discord edit failed ({response.status_code}); will recreate.")
        if response.status_code not in (404, 10008):
            raise RuntimeError(
                f"Discord message edit failed: {response.status_code} {response.text[:300]}"
            )

    response = requests.post(
        f"{WEBHOOK}?wait=true", **request_kwargs
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Discord message create failed: {response.status_code} {response.text[:300]}"
        )
    try:
        new_id = response.json().get("id")
        if not new_id:
            raise RuntimeError("Discord created the message but returned no message ID.")
        return new_id
    except ValueError as exc:
        raise RuntimeError("Discord returned an unreadable message response.") from exc


# ---------------------------------------------------------------------------
# NEWS FETCHING AND FILTERING
# ---------------------------------------------------------------------------

def fetch_feed(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    entries = []

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_node = item.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        pub_date = (item.findtext("pubDate") or "").strip()
        if title and link:
            entries.append({"title": title, "link": link, "source": source, "published": pub_date})

    if not entries:
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{ns}entry"):
            title_node = entry.find(f"{ns}title")
            link_node = entry.find(f"{ns}link")
            title = (title_node.text or "").strip() if title_node is not None else ""
            link = link_node.get("href", "") if link_node is not None else ""
            if title and link:
                entries.append({"title": title, "link": link, "source": "", "published": ""})
    return entries


def resolve_url(link):
    if "news.google.com" in link and gnewsdecoder:
        try:
            decoded = gnewsdecoder(link, interval=1)
            if decoded.get("status") and decoded.get("decoded_url"):
                return decoded["decoded_url"]
        except Exception as exc:
            log(f"Google News URL decode failed: {exc}")
    return link


def fetch_article_text(url):
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        content_type = response.headers.get("Content-Type", "").lower()
        if response.status_code != 200 or "html" not in content_type:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        return " ".join(p for p in paragraphs if len(p) > 40)[:12000]
    except Exception as exc:
        log(f"Article fetch failed: {exc}")
        return ""


def normalize_title(title):
    value = html.unescape(title).lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def story_id(title, url):
    # Headline identity is stable across different publisher URLs for the same story.
    normalized = normalize_title(title)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def domain_of(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def is_trusted(source, url):
    source_l = (source or "").lower().strip()
    domain = domain_of(url)
    if domain == "sec.gov" or domain.endswith(".sec.gov"):
        return True
    if domain in TRUSTED_SOURCES or any(domain.endswith("." + d) for d in TRUSTED_SOURCES):
        return True
    return any(name in source_l for name in TRUSTED_SOURCE_NAMES)


def is_blocked(title):
    low = title.lower()
    return any(word in low for word in BLOCK_WORDS)


def passes_filters(mode, title, source, url):
    if is_blocked(title):
        return False
    if mode == "ripple":
        # Official Ripple feed is inherently a primary source.
        return True
    if not RELEVANT_RE.search(title):
        return False
    if mode == "news":
        return is_trusted(source, url)
    return mode == "sec"


def split_sentences(text):
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if 30 < len(sentence.strip()) < 700
    ]


# ---------------------------------------------------------------------------
# NEWS ANALYSIS
# ---------------------------------------------------------------------------

def sentence_score(sentence):
    low = sentence.lower()
    positive = sum(weight for phrase, weight in BULLISH.items() if phrase in low)
    negative = sum(weight for phrase, weight in BEARISH.items() if phrase in low)

    # Basic local negation handling: "not approved", "no inflows", etc.
    if NEGATION_RE.search(low):
        positive, negative = negative, positive

    return max(-3, min(3, positive - negative))


def has_direct_xrp_demand(text):
    return any(pattern.search(text) for pattern in DIRECT_DEMAND_RE)


def analyse(title, article_text):
    article_sentences = split_sentences(article_text) if article_text else []
    relevant = [s for s in article_sentences if RELEVANT_RE.search(s)][:40]
    headline_score = sentence_score(title)
    body_scores = [sentence_score(sentence) for sentence in relevant]

    # Headline counts twice, body contributes evidence but is capped to limit repetition.
    score = headline_score * 2 + max(-5, min(5, sum(body_scores)))
    headline_only = not relevant

    evidence_text = (title + " " + " ".join(relevant[:20])).lower()
    direct_demand = has_direct_xrp_demand(evidence_text)

    threshold = 3 if headline_only else 4
    strongest = max(
        relevant,
        key=lambda sentence: abs(sentence_score(sentence)),
        default="",
    )

    # Avoid classifying a general Ripple business announcement as direct XRP demand.
    if score >= threshold:
        verdict = "🟢 GOOD FOR XRP" if direct_demand else "🟡 RIPPLE GROWTH"
    elif score <= -threshold:
        verdict = "🔴 BAD NEWS"
    elif any(word in evidence_text for word in ("sec", "court", "ruling", "regulation", "regulatory")):
        verdict = "🔵 REGULATORY"
    else:
        verdict = "🟠 MIXED / NEUTRAL"

    confidence = "Low"
    if relevant and abs(score) >= threshold + 2:
        confidence = "Medium"
    if relevant and abs(score) >= threshold + 4 and len(relevant) >= 3:
        confidence = "High"

    basis = "headline only" if headline_only else "article text"
    return {
        "score": int(score),
        "verdict": verdict,
        "key_sentence": strongest[:260],
        "basis": basis,
        "direct_demand": direct_demand,
        "confidence": confidence,
    }


def is_critical(title, analysis):
    low = title.lower()
    critical_terms = (
        "hack", "hacked", "exploit", "security breach", "fraud",
        "etf rejected", "etf rejection", "court rules", "sec ruling",
        "license revoked", "licence revoked", "bankruptcy",
    )
    return (
        any(term in low for term in critical_terms)
        or ("🔴" in analysis["verdict"] and analysis["score"] <= -6)
    )


def record_news(state, feed_name, title, url, source, analysis):
    now = time.time()
    sid = story_id(title, url)
    existing = next(
        (item for item in state["news_history"] if item.get("story_id") == sid),
        None,
    )

    if existing:
        # Another outlet has covered the same headline: record corroboration,
        # but do not count it as another independent story.
        outlets = existing.setdefault("outlets", [])
        outlet = source or domain_of(url) or feed_name
        if outlet not in outlets:
            outlets.append(outlet)
        existing["last_seen"] = now
        return False, existing

    item = {
        "story_id": sid,
        "ts": now,
        "last_seen": now,
        "title": title[:300],
        "url": url,
        "source": source or domain_of(url) or feed_name,
        "feed": feed_name,
        "category": news_category(title, feed_name),
        "verdict": analysis["verdict"],
        "score": analysis["score"],
        "confidence": analysis["confidence"],
        "direct_demand": analysis["direct_demand"],
        "key_sentence": analysis["key_sentence"],
        "basis": analysis["basis"],
        "outlets": [source or domain_of(url) or feed_name],
    }
    state["news_history"].append(item)
    return True, item


def send_critical_once(state, item):
    if not SEND_CRITICAL_NEWS_ALERTS:
        return
    if not is_critical(item["title"], {
        "verdict": item["verdict"], "score": item["score"]
    }):
        return
    sid = item["story_id"]
    if any(record.get("story_id") == sid for record in state["urgent_sent"]):
        return
    send_alert(
        f"🚨 **CRITICAL XRP NEWS — PLEASE REVIEW**\n"
        f"**{item['title']}**\n"
        f"Source: {item['source']}\n"
        f"<{item['url']}>"
    )
    state["urgent_sent"].append({"story_id": sid, "ts": time.time()})


def process_news(state):
    new_count = 0
    for feed_name, config in NEWS_FEEDS.items():
        try:
            entries = fetch_feed(config["url"])
        except Exception as exc:
            log(f"News feed '{feed_name}' failed: {exc}")
            continue

        known_links = set(state["seen_links"].get(feed_name, []))
        fresh_entries = []
        for entry in entries:
            link = entry["link"]
            if link in known_links:
                continue
            known_links.add(link)
            fresh_entries.append(entry)

        state["seen_links"][feed_name] = list(known_links)[-250:]

        if not state["initialized"].get(feed_name, False):
            state["initialized"][feed_name] = True
            # On first-ever run, process recent entries rather than silently
            # dropping the entire initial feed. Cap prevents a large first flood.
            fresh_entries = entries[:10]
            log(f"News feed '{feed_name}' initialized ({len(entries)} items).")

        processed = 0
        for entry in fresh_entries:
            title, link, source = entry["title"], entry["link"], entry["source"]
            key = normalize_title(title)
            if key in state["seen_titles"]:
                continue
            resolved = resolve_url(link)
            if not passes_filters(config["mode"], title, source, resolved):
                state["seen_titles"].append(key)
                continue

            article_text = fetch_article_text(resolved)
            result = analyse(title, article_text)
            if result["verdict"].startswith("🟠") and not SEND_MIXED:
                state["seen_titles"].append(key)
                continue

            is_new, item = record_news(
                state, feed_name, title, resolved, source, result
            )
            state["seen_titles"].append(key)
            if is_new:
                new_count += 1
                send_critical_once(state, item)
                processed += 1
                log(f"News recorded: {item['verdict']} {title[:90]}")
            if processed >= MAX_ALERTS_PER_SCAN:
                break

    state["seen_titles"] = state["seen_titles"][-500:]
    prune_state(state)
    return new_count


# ---------------------------------------------------------------------------
# EDITABLE NEWS DASHBOARD
# ---------------------------------------------------------------------------

def news_category(title, feed=""):
    """Assign each story to one display category using transparent keyword rules."""
    text = f"{title} {feed}".lower()
    rules = (
        ("ETF / Funds", ("etf", "exchange-traded", "fund inflow", "fund outflow")),
        ("Regulation", ("sec", "court", "lawsuit", "regulat", "license", "licence", "ruling")),
        ("Ripple business", ("ripple", "partnership", "partner", "acquisition", "rlusd", "prime")),
        ("Adoption / Payments", ("adoption", "payment", "on-demand liquidity", "odl", "bank", "remittance")),
        ("Security", ("hack", "hacked", "exploit", "breach", "fraud", "scam")),
    )
    for category, terms in rules:
        if any(term in text for term in terms):
            return category
    return "Other XRP news"


def build_news_chart(state):
    """Build a single two-panel PNG: sentiment timeline and news category mix."""
    if not CHARTS_AVAILABLE:
        log("Matplotlib unavailable; sending news dashboard without charts.")
        return None

    items = sorted(
        state.get("news_history", []),
        key=lambda item: float(item.get("ts", 0)),
    )
    now = time.time()
    cutoff = now - NEWS_HISTORY_HOURS * 3600
    items = [
        item for item in items
        if float(item.get("ts", 0)) >= cutoff
    ]

    fig, (ax_sentiment, ax_categories) = plt.subplots(
        1, 2, figsize=(13, 4.8), gridspec_kw={"width_ratios": [1.55, 1]}
    )
    fig.patch.set_facecolor("#101827")
    axes = (ax_sentiment, ax_categories)
    for ax in axes:
        ax.set_facecolor("#172235")
        ax.tick_params(colors="#cbd5e1", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#34445e")
        ax.grid(True, color="#34445e", alpha=0.45, linewidth=0.6)
        ax.title.set_color("#f1f5f9")
        ax.xaxis.label.set_color("#cbd5e1")
        ax.yaxis.label.set_color("#cbd5e1")

    # Left: cumulative sentiment balance over the rolling news window.
    ax_sentiment.set_title("NEWS SENTIMENT TIMELINE", fontsize=11, fontweight="bold", pad=12)
    ax_sentiment.set_ylabel("Cumulative story balance", fontsize=8)
    ax_sentiment.set_xlabel("Publication / detection time (UTC)", fontsize=8)
    ax_sentiment.axhline(0, color="#94a3b8", linewidth=0.9, alpha=0.8)
    if items:
        times = []
        balances = []
        balance = 0
        for item in items:
            verdict = item.get("verdict", "")
            if verdict.startswith("🟢"):
                balance += 1
            elif verdict.startswith("🔴"):
                balance -= 1
            times.append(datetime.fromtimestamp(float(item.get("ts", now)), timezone.utc))
            balances.append(balance)
        ax_sentiment.step(times, balances, where="post", color="#60a5fa", linewidth=2.3)
        ax_sentiment.fill_between(times, balances, 0, step="post", color="#3b82f6", alpha=0.16)
        ax_sentiment.scatter(
            times, balances,
            c=[
                "#22c55e" if item.get("verdict", "").startswith("🟢")
                else "#ef4444" if item.get("verdict", "").startswith("🔴")
                else "#f59e0b"
                for item in items
            ],
            s=24, zorder=3, edgecolors="#101827", linewidths=0.5,
        )
        ax_sentiment.xaxis.set_major_formatter(DateFormatter("%d %b\n%H:%M"))
        fig.autofmt_xdate(rotation=0, ha="center")
    else:
        ax_sentiment.text(
            0.5, 0.5, "Waiting for qualifying XRP news",
            transform=ax_sentiment.transAxes, ha="center", va="center",
            color="#cbd5e1", fontsize=10,
        )
        ax_sentiment.set_xticks([])
    ax_sentiment.margins(x=0.04)

    # Right: story count by topic.
    ax_categories.set_title("NEWS CATEGORY BREAKDOWN", fontsize=11, fontweight="bold", pad=12)
    ax_categories.set_xlabel("Independent stories", fontsize=8)
    categories = [
        "ETF / Funds", "Regulation", "Ripple business",
        "Adoption / Payments", "Security", "Other XRP news",
    ]
    counts = {category: 0 for category in categories}
    for item in items:
        category = news_category(item.get("title", ""), item.get("feed", ""))
        counts[category] = counts.get(category, 0) + 1
    values = [counts[category] for category in categories]
    palette = ["#60a5fa", "#a78bfa", "#22c55e", "#2dd4bf", "#ef4444", "#94a3b8"]
    bars = ax_categories.barh(categories, values, color=palette, height=0.62)
    ax_categories.invert_yaxis()
    ax_categories.tick_params(axis="y", labelsize=8)
    ax_categories.set_xlim(0, max(values + [1]) * 1.22)
    for bar, value in zip(bars, values):
        ax_categories.text(
            value + 0.06, bar.get_y() + bar.get_height() / 2,
            str(value), va="center", color="#f1f5f9", fontsize=8,
        )

    fig.suptitle(
        f"XRP NEWS INTELLIGENCE  ·  LAST {NEWS_HISTORY_HOURS} HOURS  ·  {len(items)} STORIES",
        color="#f8fafc", fontsize=13, fontweight="bold", y=1.02,
    )
    fig.tight_layout()
    image_buffer = BytesIO()
    fig.savefig(
        image_buffer, format="png", dpi=150, facecolor=fig.get_facecolor(),
        bbox_inches="tight",
    )
    plt.close(fig)
    image_buffer.seek(0)
    return image_buffer.getvalue()


def build_news_payload(state):
    items = sorted(
        state["news_history"],
        key=lambda item: float(item.get("ts", 0)),
        reverse=True,
    )
    positive = sum(1 for item in items if item["verdict"].startswith("🟢"))
    negative = sum(1 for item in items if item["verdict"].startswith("🔴"))
    ripple = sum(1 for item in items if item["verdict"].startswith("🟡"))
    regulatory = sum(1 for item in items if item["verdict"].startswith("🔵"))
    mixed = sum(1 for item in items if item["verdict"].startswith("🟠"))

    if positive >= 2 and negative == 0:
        overall = "🟢 POSITIVE NEWS MOMENTUM"
        color = 5763719
        summary = "At least two independent positive stories; no negative stories in the window."
    elif negative >= 1 and positive >= 1:
        overall = "🟠 MIXED CONDITIONS"
        color = 15105570
        summary = "Positive and negative developments are both present."
    elif negative >= 1:
        overall = "🔴 NEGATIVE NEWS PRESSURE"
        color = 15548997
        summary = "Negative developments detected in the rolling news window."
    elif positive >= 2:
        overall = "🟢 POSITIVE NEWS MOMENTUM"
        color = 5763719
        summary = "Multiple independent positive developments detected."
    else:
        overall = "🟡 NO STRONG CONFIRMATION"
        color = 16776960
        summary = "Not enough independent evidence for a strong directional conclusion."

    lines = []
    for item in items[:MAX_DISPLAY_NEWS]:
        stamp = datetime.fromtimestamp(
            float(item["ts"]), timezone.utc
        ).strftime("%d %b %H:%M UTC")
        source = item.get("source", "Unknown source")
        title = item.get("title", "Untitled")
        lines.append(
            f"{item['verdict']} **{title[:180]}**\n"
            f"`{source[:70]} · {stamp} · {item['confidence']} confidence`"
        )

    latest_text = "\n\n".join(lines) if lines else "No qualifying XRP/Ripple news recorded yet."
    if len(latest_text) > 3900:
        latest_text = latest_text[:3850] + "\n…"

    embed = {
        "title": "📰 XRP News Intelligence",
        "description": (
            f"**{overall}**\n{summary}\n\n"
            f"**Rolling {NEWS_HISTORY_HOURS}-hour independent story count**"
        ),
        "color": color,
        "fields": [
            {
                "name": "🟢 Positive",
                "value": str(positive),
                "inline": True,
            },
            {
                "name": "🔴 Negative",
                "value": str(negative),
                "inline": True,
            },
            {
                "name": "🟡 Ripple growth",
                "value": str(ripple),
                "inline": True,
            },
            {
                "name": "🔵 Regulatory",
                "value": str(regulatory),
                "inline": True,
            },
            {
                "name": "🟠 Mixed / neutral",
                "value": str(mixed),
                "inline": True,
            },
            {
                "name": "📚 Latest developments",
                "value": latest_text[:4000],
                "inline": False,
            },
        ],
        "image": {"url": "attachment://xrp_news_analytics.png"},
        "footer": {
            "text": (
                f"Rolling {NEWS_HISTORY_HOURS}h · "
                "Independent stories only · News sentiment is not a buy/sell signal"
            )
        },
    }
    return {
        "username": "XRP News Intelligence",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }


def update_news_dashboard(state, force=False):
    payload = build_news_payload(state)
    chart = build_news_chart(state)
    files = (
        [("xrp_news_analytics.png", chart, "image/png")]
        if chart else None
    )
    if not chart:
        # Avoid a broken attachment reference when chart generation is unavailable.
        payload["embeds"][0].pop("image", None)

    signature = json.dumps(payload["embeds"], sort_keys=True, ensure_ascii=False)
    if not force and signature == state.get("last_news_render"):
        return

    try:
        previous_id = state.get("news_message_id")
        updated_id = create_or_edit_webhook_message(
            previous_id, payload, files=files
        )
        if updated_id:
            state["news_message_id"] = updated_id
            state["last_news_render"] = signature
            save_state(state)
        else:
            log("News dashboard was not confirmed by Discord; will retry next scan.")
    except Exception as exc:
        log(f"News dashboard update failed: {exc}")


def update_price_dashboard(payload, message_id):
    try:
        return create_or_edit_webhook_message(message_id, payload)
    except Exception as exc:
        log(f"Price dashboard update failed: {exc}")
        return message_id


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    if not WEBHOOK:
        log("ERROR: set DISCORD_WEBHOOK_URL in GitHub repository secrets.")
        sys.exit(1)

    state = load_state()
    prune_state(state)
    save_state(state)

    log("XRP monitor starting.")
    log(f"Price source: {'CoinMarketCap with CoinLore backup' if CMC_KEY else 'CoinLore backup only'}")
    log(f"News history: {NEWS_HISTORY_HOURS} hours; news dashboard edits one Discord message.")
    log(f"Webhook configured; state file: {STATE_FILE}")

    price_state = {"next_cmc": 0, "cmc": None}
    price_message_id = None
    price_history = deque()
    previous_level_state = None
    last_move_alert = 0
    last_news_check = 0

    # Post an initial news dashboard or recover/edit the previous one.
    update_news_dashboard(state, force=True)

    for cycle in range(RUN_MINUTES):
        now = time.time()
        data, fresh = get_prices(state, price_state)

        if data:
            price = data["price_zar"]
            price_usd = data["price_usd"]
            price_message_id = update_price_dashboard(
                build_price_payload(data), price_message_id
            )

            reached = (
                price <= BUY_LEVEL_ZAR
                if ALERT_DIRECTION == "below"
                else price >= BUY_LEVEL_ZAR
            )
            if previous_level_state is False and reached:
                send_alert(
                    f"🚨 **XRP reached your configured level!** "
                    f"R {price:,.2f} (${price_usd:,.4f}) is "
                    f"{ALERT_DIRECTION} R {BUY_LEVEL_ZAR:.2f}. "
                    f"Source: {data['source']}. Review your risk plan."
                )
            previous_level_state = reached

            if fresh:
                price_history.append((now, price_usd))
                while price_history and now - price_history[0][0] > 3900:
                    price_history.popleft()
                if (
                    PCT_MOVE_ALERT > 0
                    and price_history
                    and now - price_history[0][0] >= 3300
                ):
                    change = (
                        (price_usd - price_history[0][1])
                        / price_history[0][1] * 100
                    )
                    if abs(change) >= PCT_MOVE_ALERT and now - last_move_alert > 3600:
                        arrow = "📈" if change > 0 else "📉"
                        send_alert(
                            f"{arrow} XRP moved {change:+.1f}% in roughly one hour: "
                            f"R {price:,.2f} (${price_usd:,.4f})"
                        )
                        last_move_alert = now

        if now - last_news_check >= NEWS_CHECK_EVERY * 60 or cycle == 0:
            log("Checking XRP/Ripple news feeds...")
            new_items = process_news(state)
            update_news_dashboard(state, force=(new_items > 0))
            last_news_check = now
            save_state(state)

        if cycle % 10 == 0:
            save_state(state)
        time.sleep(60)

    save_state(state)
    log("XRP monitor run completed; state saved.")


if __name__ == "__main__":
    main()
