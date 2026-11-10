import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque

import requests

# ===== SETTINGS =====
HARDCODED_WEBHOOK = "https://discord.com/api/webhooks/1564239707515260938/MdCL16AZn4Yfydf6iWyFr0FAr-26ldTtLwotiM6YhShtWpa-1tQGtZZh7NmFjGgHlUCf"   # keep your existing one here
DISCORD_USER_ID = ""        # keep your Discord user ID here
BUY_LEVEL_ZAR = 21.50
ALERT_DIRECTION = "above"   # "above" or "below"
PCT_MOVE_ALERT = 3.0        # % move within 1 hour (0 = off)
NEWS_CHECK_EVERY = 5        # minutes

# mode "sec"    = only if headline mentions Ripple/XRP
# mode "ripple" = official Ripple posts, always send
# mode "news"   = must mention Ripple/XRP AND come from a trusted outlet
NEWS_FEEDS = {
    "SEC": {"url": "https://www.sec.gov/news/pressreleases.rss", "mode": "sec"},
    "Ripple": {"url": "https://ripple.com/insights/feed/", "mode": "ripple"},
    "XRP News": {
        "url": "https://news.google.com/rss/search?q=XRP+OR+%22Ripple+Labs%22+when:1d&hl=en-US&gl=US&ceid=US:en",
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
# ====================

RELEVANT_RE = re.compile(r"\bxrp\b|\bripple\b|garlinghouse|rlusd", re.I)

WEBHOOK = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
if not WEBHOOK and HARDCODED_WEBHOOK.startswith("https://"):
    WEBHOOK = HARDCODED_WEBHOOK.strip()

HEADERS = {"User-Agent": "Mozilla/5.0 (xrp-price-monitor; personal use)"}


def log(*args):
    print(*args, flush=True)


def get_prices():
    try:
        r = requests.get("https://api.coinlore.net/api/ticker/?id=58", headers=HEADERS, timeout=10)
        coin = r.json()[0]
        price_usd = float(coin["price_usd"])
        mcap_usd = float(coin["market_cap_usd"])
    except Exception as e:
        log(f"Crypto fetch fail: {e}")
        return None
    try:
        f = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=10)
        usd_to_zar = float(f.json()["rates"]["ZAR"])
    except Exception as e:
        log(f"Fiat fetch fail: {e}")
        usd_to_zar = 18.00
    return price_usd, mcap_usd, usd_to_zar


def build_payload(price_usd, mcap_usd, usd_to_zar):
    return {
        "username": "XRP Live Dashboard",
        "embeds": [{
            "title": "⚡ Live XRP Market Ticker",
            "color": 5763719,
            "fields": [
                {"name": "💵 Price (USD)", "value": f"${price_usd:,.4f}", "inline": True},
                {"name": "🇿🇦 Price (ZAR)", "value": f"R {price_usd * usd_to_zar:,.2f}", "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": False},
                {"name": "📈 Market Cap (USD)", "value": f"${mcap_usd:,.0f}", "inline": True},
                {"name": "🇿🇦 Market Cap (ZAR)", "value": f"R {mcap_usd * usd_to_zar:,.0f}", "inline": True},
            ],
            "footer": {"text": f"Buy alert: R {BUY_LEVEL_ZAR:.2f} | 1 USD = R {usd_to_zar:.2f}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }


def send_alert(text):
    mention = f"<@{DISCORD_USER_ID}> " if DISCORD_USER_ID else ""
    payload = {
        "username": "XRP Alerts",
        "content": f"{mention}{text}",
        "allowed_mentions": {"users": [DISCORD_USER_ID]} if DISCORD_USER_ID else {"parse": []},
    }
    try:
        res = requests.post(WEBHOOK, json=payload, timeout=10)
        if res.status_code not in (200, 204):
            log("Alert failed:", res.status_code, res.text)
    except Exception as e:
        log(f"Alert error: {e}")


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


def is_relevant(title):
    t = title.lower()
    return bool(RELEVANT_RE.search(title)) and "ripple effect" not in t


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
            tag = "🚨 " if is_priority(title) else ""
            send_alert(f"{tag}📰 **{name}**: {title}\n{link}")
            sent += 1
            if sent >= 5:
                break


def main():
    if not WEBHOOK:
        log("ERROR: no webhook found. Set the DISCORD_WEBHOOK_URL secret or paste it into HARDCODED_WEBHOOK.")
        sys.exit(1)

    log(f"Webhook found (length {len(WEBHOOK)}). Starting ticker loop...")
    message_id = None
    history = deque(maxlen=61)
    prev_state = None
    last_move_alert = 0
    seen_links = {n: set() for n in NEWS_FEEDS}
    seen_titles = set()
    initialized = {n: False for n in NEWS_FEEDS}

    for i in range(210):  # about 3.5 hours
        data = get_prices()
        if data:
            price_usd, mcap_usd, usd_to_zar = data
            price_zar = price_usd * usd_to_zar

            payload = build_payload(price_usd, mcap_usd, usd_to_zar)
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
                        log(f"Updated OK (cycle {i}) price R{price_zar:.2f}")
            except Exception as e:
                log(f"Discord error: {e}")

            reached = price_zar <= BUY_LEVEL_ZAR if ALERT_DIRECTION == "below" else price_zar >= BUY_LEVEL_ZAR
            if prev_state is False and reached:
                send_alert(
                    f"🚨 **XRP hit your level!** R {price_zar:,.2f} (${price_usd:,.4f}) "
                    f"is {ALERT_DIRECTION} R {BUY_LEVEL_ZAR:.2f}. Check your checklist before buying on Luno."
                )
            prev_state = reached

            history.append(price_usd)
            if PCT_MOVE_ALERT > 0 and len(history) == history.maxlen:
                change = (price_usd - history[0]) / history[0] * 100
                if abs(change) >= PCT_MOVE_ALERT and time.time() - last_move_alert > 3600:
                    arrow = "📈" if change > 0 else "📉"
                    send_alert(f"{arrow} XRP moved {change:+.1f}% in the last hour: R {price_zar:,.2f} (${price_usd:,.4f})")
                    last_move_alert = time.time()

        if i % NEWS_CHECK_EVERY == 0:
            check_news(seen_links, seen_titles, initialized)

        time.sleep(60)


if __name__ == "__main__":
    main()