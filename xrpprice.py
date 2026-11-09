import os
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque

import requests

# ===== SETTINGS =====
HARDCODED_WEBHOOK = "https://discord.com/api/webhooks/1564239707515260938/MdCL16AZn4Yfydf6iWyFr0FAr-26ldTtLwotiM6YhShtWpa-1tQGtZZh7NmFjGgHlUCf"   # keep your existing one here
DISCORD_USER_ID = ""        # optional: paste your Discord user ID to get @mentioned
BUY_LEVEL_ZAR = 21.50       # alert when price crosses this level (in rand)
ALERT_DIRECTION = "above"   # "above" = alert when price rises to the level, "below" = when it falls to it
PCT_MOVE_ALERT = 3.0        # also alert on a move of this % within 1 hour (set 0 to turn off)
NEWS_CHECK_EVERY = 5        # check news every 5 minutes

NEWS_FEEDS = {
    "SEC": "https://www.sec.gov/news/pressreleases.rss",
    "Ripple": "https://ripple.com/insights/feed/",
    "XRP News": "https://news.google.com/rss/search?q=Ripple+XRP+SEC+when:1d&hl=en-US&gl=US&ceid=US:en",
}
# ====================

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
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):  # RSS
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if title and link:
            items.append((title, link))
    if not items:  # Atom fallback
        ns = "{http://www.w3.org/2005/Atom}"
        for e in root.iter(f"{ns}entry"):
            title = (e.findtext(f"{ns}title") or "").strip()
            l = e.find(f"{ns}link")
            link = l.get("href") if l is not None else ""
            if title and link:
                items.append((title, link))
    return items


def check_news(seen, initialized):
    for name, url in NEWS_FEEDS.items():
        try:
            items = fetch_feed(url)
        except Exception as e:
            log(f"News feed '{name}' failed: {e}")
            continue
        new = [(t, l) for t, l in items if l not in seen[name]]
        for t, l in items:
            seen[name].add(l)
        if not initialized[name]:
            initialized[name] = True  # first load: remember existing items, don't flood the channel
            log(f"News feed '{name}' ready ({len(items)} items)")
            continue
        for t, l in new[:5]:
            send_alert(f"📰 **{name}**: {t}\n{l}")


def main():
    if not WEBHOOK:
        log("ERROR: no webhook found. Set the DISCORD_WEBHOOK_URL secret or paste it into HARDCODED_WEBHOOK.")
        sys.exit(1)

    log(f"Webhook found (length {len(WEBHOOK)}). Starting ticker loop...")
    message_id = None
    history = deque(maxlen=61)   # about 1 hour of 1-minute prices
    prev_state = None            # whether price was past the buy level last cycle
    last_move_alert = 0
    seen = {n: set() for n in NEWS_FEEDS}
    initialized = {n: False for n in NEWS_FEEDS}

    for i in range(210):  # about 3.5 hours, then exit so the next scheduled run takes over
        data = get_prices()
        if data:
            price_usd, mcap_usd, usd_to_zar = data
            price_zar = price_usd * usd_to_zar

            # ---- live embed (edited quietly) ----
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

            # ---- buy-level alert (fires once per crossing) ----
            if ALERT_DIRECTION == "below":
                reached = price_zar <= BUY_LEVEL_ZAR
            else:
                reached = price_zar >= BUY_LEVEL_ZAR
            if prev_state is False and reached:
                send_alert(
                    f"🚨 **XRP hit your level!** R {price_zar:,.2f} "
                    f"(${price_usd:,.4f}) is {ALERT_DIRECTION} R {BUY_LEVEL_ZAR:.2f}. "
                    f"Check your checklist before buying on Luno."
                )
            prev_state = reached

            # ---- fast-move alert ----
            history.append(price_usd)
            if PCT_MOVE_ALERT > 0 and len(history) == history.maxlen:
                change = (price_usd - history[0]) / history[0] * 100
                if abs(change) >= PCT_MOVE_ALERT and time.time() - last_move_alert > 3600:
                    arrow = "📈" if change > 0 else "📉"
                    send_alert(f"{arrow} XRP moved {change:+.1f}% in the last hour: R {price_zar:,.2f} (${price_usd:,.4f})")
                    last_move_alert = time.time()

        # ---- news ----
        if i % NEWS_CHECK_EVERY == 0:
            check_news(seen, initialized)

        time.sleep(60)


if __name__ == "__main__":
    main()