import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque

import requests
from bs4 import BeautifulSoup

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None

# ===== SETTINGS =====
HARDCODED_WEBHOOK = "https://discord.com/api/webhooks/1564239707515260938/MdCL16AZn4Yfydf6iWyFr0FAr-26ldTtLwotiM6YhShtWpa-1tQGtZZh7NmFjGgHlUCf"   # paste your webhook here
HARDCODED_CMC_KEY = ""      # optional: prefer the CMC_API_KEY GitHub secret instead
DISCORD_USER_ID = ""        # paste your Discord user ID here
BUY_LEVEL_ZAR = 21.50
ALERT_DIRECTION = "above"   # "above" or "below"
PCT_MOVE_ALERT = 3.0        # % move within 1 hour (0 = off)
CMC_EVERY_MINUTES = 10      # how often to ask CoinMarketCap (free plan has a monthly limit)
NEWS_CHECK_EVERY = 5        # minutes
SEND_MIXED = True           # False = only send clearly good or bad news
# ====================

CMC_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
CMC_XRP_ID = "52"

NEWS_FEEDS = {
    "SEC": {"url": "https://www.sec.gov/news/pressreleases.rss", "mode": "sec"},
    "Ripple": {"url": "https://ripple.com/insights/feed/", "mode": "ripple"},
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

TRUSTED_SOURCES = [
    "reuters", "bloomberg", "coindesk", "the block", "cointelegraph", "decrypt",
    "cnbc", "wall street journal", "wsj", "financial times", "forbes", "fortune",
    "yahoo finance", "marketwatch", "barron", "ripple", "sec.gov", "securities and exchange",
]

BLOCK_WORDS = [
    "price prediction", "price forecast", "predicts", "prediction", "could hit", "will hit",
    "to the moon", "giveaway", "airdrop", "presale", "pre-sale", "100x", "1000x", "next bitcoin",
    "best crypto to buy", "top 5", "top 10", "meme coin", "memecoin", "buy now", "millionaire",
    "sponsored", "advertorial", "get rich", "passive income", "price analysis",
]

PRIORITY_WORDS = [
    "approved", "approval", "approves", "lawsuit", "ruling", "court", "etf", "settlement",
    "license", "licence", "acquisition", "acquires", "partnership", "delist", "hack",
    "breach", "injunction", "appeal", "dismiss", "charges", "launch",
]

BULLISH = {
    "net inflow": 2, "inflows": 2, "etf approved": 3, "approves": 2, "approved": 2, "approval": 1,
    "drops lawsuit": 3, "dismissed": 2, "dismisses": 2, "drops appeal": 3, "wins": 2, "settles": 2,
    "partnership": 2, "partners with": 2, "adopts": 2, "adoption": 1, "integrates": 2,
    "licence granted": 3, "license granted": 3, "granted a licence": 3, "granted a license": 3,
    "regulatory clarity": 2, "launches": 1, "launch": 1, "acquires": 2, "acquisition": 1,
    "record high": 2, "all-time high": 2, "surge": 1, "rallies": 1, "rlusd": 1, "bullish": 1,
}
BEARISH = {
    "net outflow": 2, "outflows": 2, "rejected": 2, "rejects": 2, "delays": 2, "delayed": 2,
    "hack": 3, "hacked": 3, "breach": 3, "exploit": 2, "sues": 2, "sued": 2, "lawsuit filed": 2,
    "fraud": 3, "investigation": 2, "fined": 2, "fine": 1, "charges": 2, "ban": 2, "bans": 2,
    "crash": 2, "plunge": 2, "plunges": 2, "sell-off": 1, "selloff": 1, "dump": 1, "delist": 3,
    "bearish": 1, "warns": 1, "concern": 1, "uncertainty": 1,
}

RELEVANT_RE = re.compile(r"\bxrp\b|\bripple\b|garlinghouse|rlusd", re.I)

WEBHOOK = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
if not WEBHOOK and HARDCODED_WEBHOOK.startswith("https://"):
    WEBHOOK = HARDCODED_WEBHOOK.strip()

CMC_KEY = (os.getenv("CMC_API_KEY") or "").strip() or HARDCODED_CMC_KEY.strip()

HEADERS = {"User-Agent": "Mozilla/5.0 (xrp-price-monitor; personal use)"}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def log(*args):
    print(*args, flush=True)


# ---------- prices ----------
def fetch_cmc():
    r = requests.get(
        CMC_URL,
        headers={"X-CMC_PRO_API_KEY": CMC_KEY, "Accept": "application/json"},
        params={"id": CMC_XRP_ID, "convert": "USD,ZAR"},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()["data"][CMC_XRP_ID]
    if isinstance(d, list):
        d = d[0]
    q = d["quote"]
    usd, zar = q["USD"], q["ZAR"]
    return {
        "price_usd": float(usd["price"]),
        "price_zar": float(zar["price"]),
        "mcap_usd": float(usd.get("market_cap") or 0),
        "mcap_zar": float(zar.get("market_cap") or 0),
        "usd_to_zar": float(zar["price"]) / float(usd["price"]),
        "source": "CoinMarketCap",
    }


def fetch_coinlore():
    r = requests.get("https://api.coinlore.net/api/ticker/?id=58", headers=HEADERS, timeout=10)
    coin = r.json()[0]
    price_usd = float(coin["price_usd"])
    mcap_usd = float(coin["market_cap_usd"])
    try:
        f = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=10)
        usd_to_zar = float(f.json()["rates"]["ZAR"])
    except Exception as e:
        log(f"Fiat fetch fail: {e}")
        usd_to_zar = 18.00
    return {
        "price_usd": price_usd,
        "price_zar": price_usd * usd_to_zar,
        "mcap_usd": mcap_usd,
        "mcap_zar": mcap_usd * usd_to_zar,
        "usd_to_zar": usd_to_zar,
        "source": "CoinLore (backup)",
    }


def get_prices(state):
    """Returns (data, fresh). CoinMarketCap is polled every CMC_EVERY_MINUTES, CoinLore is the backup."""
    now = time.time()

    if CMC_KEY and now >= state["next_cmc"]:
        try:
            data = fetch_cmc()
            data["time"] = now
            state["cmc"] = data
            state["next_cmc"] = now + CMC_EVERY_MINUTES * 60
            return data, True
        except Exception as e:
            log(f"CoinMarketCap fetch failed: {e}")
            state["next_cmc"] = now + 120  # retry in 2 minutes

    cmc = state.get("cmc")
    if cmc and now - cmc["time"] < 30 * 60:
        return cmc, False  # still fresh enough, reuse

    try:  # backup source
        data = fetch_coinlore()
        data["time"] = now
        return data, True
    except Exception as e:
        log(f"CoinLore fetch failed: {e}")
        return None, False


def build_payload(d):
    updated = time.strftime("%H:%M", time.gmtime(d["time"]))
    return {
        "username": "XRP Live Dashboard",
        "embeds": [{
            "title": "⚡ Live XRP Market Ticker",
            "color": 5763719,
            "fields": [
                {"name": "💵 Price (USD)", "value": f"${d['price_usd']:,.4f}", "inline": True},
                {"name": "🇿🇦 Price (ZAR)", "value": f"R {d['price_zar']:,.2f}", "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": False},
                {"name": "📈 Market Cap (USD)", "value": f"${d['mcap_usd']:,.0f}", "inline": True},
                {"name": "🇿🇦 Market Cap (ZAR)", "value": f"R {d['mcap_zar']:,.0f}", "inline": True},
            ],
            "footer": {"text": f"{d['source']} · price as of {updated} UTC · buy alert R {BUY_LEVEL_ZAR:.2f}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }


def send_alert(text):
    mention = f"<@{DISCORD_USER_ID}> " if DISCORD_USER_ID else ""
    payload = {
        "username": "XRP Alerts",
        "content": f"{mention}{text}"[:1990],
        "allowed_mentions": {"users": [DISCORD_USER_ID]} if DISCORD_USER_ID else {"parse": []},
    }
    try:
        res = requests.post(WEBHOOK, json=payload, timeout=10)
        if res.status_code not in (200, 204):
            log("Alert failed:", res.status_code, res.text)
    except Exception as e:
        log(f"Alert error: {e}")


# ---------- news fetching ----------
def fetch_feed(url):
    """Returns a list of (title, link, source)."""
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):  # RSS
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        source = (it.findtext("source") or "").strip()
        if title and link:
            items.append((title, link, source))
    if not items:  # Atom fallback
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.iter(f"{ns}entry"):
            title = (e.findtext(f"{ns}title") or "").strip()
            l = e.find(f"{ns}link")
            link = l.get("href") if l is not None else ""
            if title and link:
                items.append((title, link, ""))
    return items


def resolve_url(link):
    """Google News links are redirects; decode them to the real article URL."""
    if "news.google.com" in link and gnewsdecoder:
        try:
            res = gnewsdecoder(link, interval=1)
            if res.get("status") and res.get("decoded_url"):
                return res["decoded_url"]
        except Exception as e:
            log(f"Could not decode Google link: {e}")
    return link


def fetch_article_text(url):
    """Returns the article's paragraph text, or '' if it can't be read (paywall, block, error)."""
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=12)
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", ""):
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(p for p in paras if len(p) > 40)
        return text[:10000]
    except Exception as e:
        log(f"Article fetch failed: {e}")
        return ""


# ---------- filters ----------
def is_relevant(title):
    return bool(RELEVANT_RE.search(title)) and "ripple effect" not in title.lower()


def is_blocked(title):
    t = title.lower()
    return any(b in t for b in BLOCK_WORDS)


def is_trusted(title, source):
    text = f"{source} {title}".lower()
    return any(s in text for s in TRUSTED_SOURCES)


def is_priority(title):
    t = title.lower()
    return any(p in t for p in PRIORITY_WORDS)


def passes_filters(mode, title, source):
    if mode == "ripple":
        return True
    if not is_relevant(title) or is_blocked(title):
        return False
    if mode == "news":
        return is_trusted(title, source)
    return True  # sec: relevant to Ripple/XRP is enough


# ---------- analysis ----------
def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 30 < len(s.strip()) < 600]


def analyse(title, text):
    """Returns (score, verdict, key_sentence, basis)."""
    article_sentences = split_sentences(text) if text else []
    relevant = [s for s in article_sentences if RELEVANT_RE.search(s)][:40]
    sentences = [title, title] + relevant  # headline counts double

    score = 0
    best = (0, "")
    for s in sentences:
        low = s.lower()
        s_score = 0
        for phrase, w in BULLISH.items():
            if phrase in low:
                s_score += w
        for phrase, w in BEARISH.items():
            if phrase in low:
                s_score -= w
        s_score = max(-3, min(3, s_score))  # cap per sentence
        score += s_score
        if abs(s_score) > best[0] and s != title:
            best = (abs(s_score), s)

    headline_only = not relevant
    threshold = 2 if headline_only else 3
    if score >= threshold:
        verdict = "🟢 GOOD NEWS"
    elif score <= -threshold:
        verdict = "🔴 BAD NEWS"
    else:
        verdict = "🟡 MIXED / NEUTRAL"

    key = best[1] or (relevant[0] if relevant else "")
    if len(key) > 220:
        key = key[:217].rstrip() + "..."
    basis = "headline only" if headline_only else "read full article"
    return score, verdict, key, basis


def check_news(seen_links, seen_titles, initialized):
    for name, cfg in NEWS_FEEDS.items():
        try:
            items = fetch_feed(cfg["url"])
        except Exception as e:
            log(f"News feed '{name}' failed: {e}")
            continue

        new = []
        for title, link, source in items:
            key = re.sub(r"\W+", " ", title.lower()).strip()
            if link in seen_links[name] or key in seen_titles:
                seen_links[name].add(link)
                continue
            seen_links[name].add(link)
            seen_titles.add(key)
            new.append((title, link, source))

        if not initialized[name]:
            initialized[name] = True  # first load: remember existing items, don't flood the channel
            log(f"News feed '{name}' ready ({len(items)} items)")
            continue

        sent = 0
        for title, link, source in new:
            if not passes_filters(cfg["mode"], title, source):
                continue

            url = resolve_url(link)
            text = fetch_article_text(url)
            score, verdict, key, basis = analyse(title, text)

            if verdict.startswith("🟡") and not SEND_MIXED:
                continue

            tag = "🚨 " if is_priority(title) else ""
            src = f" ({source})" if source else ""
            lines = [f"{tag}{verdict} · {name}{src}", f"**{title}**"]
            if key:
                lines.append(f"> {key}")
            lines.append(f"*Score {score:+d} · {basis}*")
            lines.append(f"<{url}>")
            send_alert("\n".join(lines))
            log(f"News alert sent: {verdict} {title[:60]}")

            sent += 1
            if sent >= 5:
                break


# ---------- main loop ----------
def main():
    if not WEBHOOK:
        log("ERROR: no webhook found. Set the DISCORD_WEBHOOK_URL secret or paste it into HARDCODED_WEBHOOK.")
        sys.exit(1)

    log(f"Webhook found (length {len(WEBHOOK)}).")
    if CMC_KEY:
        log(f"CoinMarketCap key found. Polling every {CMC_EVERY_MINUTES} min.")
    else:
        log("No CMC_API_KEY found, using CoinLore backup prices only.")
    log("Starting ticker loop...")

    state = {"next_cmc": 0, "cmc": None}
    message_id = None
    history = deque()            # (timestamp, price_usd) for the 1-hour move alert
    prev_state = None
    last_move_alert = 0
    seen_links = {n: set() for n in NEWS_FEEDS}
    seen_titles = set()
    initialized = {n: False for n in NEWS_FEEDS}

    for i in range(210):  # about 3.5 hours
        d, fresh = get_prices(state)
        if d:
            price_zar = d["price_zar"]
            price_usd = d["price_usd"]

            payload = build_payload(d)
            try:
                if message_id is None:
                    res = requests.post(f"{WEBHOOK}?wait=true", json=payload, timeout=10)
                    if res.status_code in (200, 201):
                        message_id = res.json().get("id")
                        log(f"Posted message {message_id}")
                    else:
                        log("Discord post failed:", res.status_code, res.text)
                        if res.status_code in (401, 404):
                            sys.exit(1)
                else:
                    res = requests.patch(f"{WEBHOOK}/messages/{message_id}", json=payload, timeout=10)
                    if res.status_code not in (200, 204):
                        log("Discord edit failed:", res.status_code, res.text)
                        message_id = None
                    elif i % 30 == 0:
                        log(f"Updated OK (cycle {i}) R{price_zar:.2f} via {d['source']}")
            except Exception as e:
                log(f"Discord error: {e}")

            reached = price_zar <= BUY_LEVEL_ZAR if ALERT_DIRECTION == "below" else price_zar >= BUY_LEVEL_ZAR
            if prev_state is False and reached:
                send_alert(
                    f"🚨 **XRP hit your level!** R {price_zar:,.2f} (${price_usd:,.4f}) "
                    f"is {ALERT_DIRECTION} R {BUY_LEVEL_ZAR:.2f}. Source: {d['source']}. "
                    f"Check your checklist before buying on Luno."
                )
            prev_state = reached

            if fresh:
                now = time.time()
                history.append((now, price_usd))
                while history and now - history[0][0] > 3900:
                    history.popleft()
                if PCT_MOVE_ALERT > 0 and history and now - history[0][0] >= 3300:
                    change = (price_usd - history[0][1]) / history[0][1] * 100
                    if abs(change) >= PCT_MOVE_ALERT and now - last_move_alert > 3600:
                        arrow = "📈" if change > 0 else "📉"
                        send_alert(f"{arrow} XRP moved {change:+.1f}% in the last hour: R {price_zar:,.2f} (${price_usd:,.4f})")
                        last_move_alert = now

        if i % NEWS_CHECK_EVERY == 0:
            check_news(seen_links, seen_titles, initialized)

        time.sleep(60)


if __name__ == "__main__":
    main()