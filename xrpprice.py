import os
import time
import requests

def fetch_and_notify():
    webhook_url = os.getenv("https://discord.com/api/webhooks/1564239707515260938/MdCL16AZn4Yfydf6iWyFr0FAr-26ldTtLwotiM6YhShtWpa-1tQGtZZh7NmFjGgHlUCf")
    
    # 1. Fetch data from CoinLore (No GitHub IP blocking)
    try:
        coin_res = requests.get("https://coinlore.net", timeout=10)
        coin_data = coin_res.json()
        price_usd = float(coin_data.get("price_usd", 0))
        mcap_usd = float(coin_data.get("market_cap_usd", 0))
    except Exception as e:
        print(f"Crypto fetch fail: {e}")
        return None

    # 2. Fetch Currency Conversion
    try:
        fiat_res = requests.get("https://er-api.com", timeout=10)
        usd_to_zar = float(fiat_res.json().get("rates", {}).get("ZAR", 18.00))
    except Exception as e:
        print(f"Fiat fetch fail: {e}")
        usd_to_zar = 18.00

    price_zar = price_usd * usd_to_zar
    mcap_zar = mcap_usd * usd_to_zar

    # 3. Payload Build
    payload = {
        "username": "XRP Live Dashboard",
        "embeds": [{
            "title": "⚡ Live XRP Market Ticker (Updates Every 1 Min)",
            "color": 5763719, 
            "fields": [
                {"name": "💵 Price (USD)", "value": f"${price_usd:,.4f}", "inline": True},
                {"name": "🇿🇦 Price (ZAR)", "value": f"R {price_zar:,.2f}", "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": False},
                {"name": "📈 Market Cap (USD)", "value": f"${mcap_usd:,.2f}", "inline": True},
                {"name": "🇿🇦 Market Cap (ZAR)", "value": f"R {mcap_zar:,.2f}", "inline": True}
            ],
            "footer": {
                "text": f"Last Live Refresh • 1 USD = R {usd_to_zar:.2f}"
            },
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }]
    }
    return payload

def main():
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    message_id = None
    print("Starting 1-minute tracking loop...")
    
    # Runs for ~3.5 hours, then finishes so GitHub Actions cleans up safely
    for _ in range(210):
        payload = fetch_and_notify()
        if payload:
            try:
                if not message_id:
                    res = requests.post(f"{webhook_url}?wait=true", json=payload, timeout=10)
                    if res.status_code in (200, 201, 204):
                        message_id = res.json().get("id")
                else:
                    requests.patch(f"{webhook_url}/messages/{message_id}", json=payload, timeout=10)
            except Exception as e:
                print(f"Discord Webhook error: {e}")
                
        time.sleep(60)

if __name__ == "__main__":
    main()
