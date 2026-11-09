import os
import sys
import time
import requests

# ===== PASTE YOUR DISCORD WEBHOOK URL BETWEEN THE QUOTES BELOW =====
HARDCODED_WEBHOOK = "https://discord.com/api/webhooks/1564239707515260938/MdCL16AZn4Yfydf6iWyFr0FAr-26ldTtLwotiM6YhShtWpa-1tQGtZZh7NmFjGgHlUCf"
# ====================================================================

WEBHOOK = (os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
if not WEBHOOK and HARDCODED_WEBHOOK.startswith("https://"):
    WEBHOOK = HARDCODED_WEBHOOK.strip()


def log(*args):
    print(*args, flush=True)


def build_payload():
    try:
        r = requests.get("https://api.coinlore.net/api/ticker/?id=58", timeout=10)
        coin = r.json()[0]
        price_usd = float(coin["price_usd"])
        mcap_usd = float(coin["market_cap_usd"])
    except Exception as e:
        log(f"Crypto fetch fail: {e}")
        return None

    try:
        f = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        usd_to_zar = float(f.json()["rates"]["ZAR"])
    except Exception as e:
        log(f"Fiat fetch fail: {e}")
        usd_to_zar = 18.00

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
            "footer": {"text": f"1 USD = R {usd_to_zar:.2f}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }


def main():
    if not WEBHOOK:
        log("ERROR: no webhook found. Set the DISCORD_WEBHOOK_URL secret or paste it into HARDCODED_WEBHOOK.")
        sys.exit(1)

    log(f"Webhook found (length {len(WEBHOOK)}). Starting ticker loop...")
    message_id = None

    for i in range(210):  # about 3.5 hours, then exit so the next scheduled run takes over
        payload = build_payload()
        if payload:
            try:
                if message_id is None:
                    res = requests.post(f"{WEBHOOK}?wait=true", json=payload, timeout=10)
                    if res.status_code in (200, 201):
                        message_id = res.json().get("id")
                        log(f"Posted message {message_id}")
                    else:
                        log("Discord post failed:", res.status_code, res.text)
                        if res.status_code in (401, 404):
                            sys.exit(1)  # bad or deleted webhook, fail loudly
                else:
                    res = requests.patch(f"{WEBHOOK}/messages/{message_id}", json=payload, timeout=10)
                    if res.status_code not in (200, 204):
                        log("Discord edit failed:", res.status_code, res.text)
                        message_id = None  # post a fresh message next cycle
                    elif i % 30 == 0:
                        log(f"Updated OK (cycle {i})")
            except Exception as e:
                log(f"Discord error: {e}")
        time.sleep(60)


if __name__ == "__main__":
    main()