import requests
import time
from datetime import datetime

# ====== PASTE YOUR WEBHOOK HERE ======
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1564239707515260938/MdCL16AZn4Yfydf6iWyFr0FAr-26ldTtLwotiM6YhShtWpa-1tQGtZZh7NmFjGgHlUCf"
# =====================================

CHECK_EVERY = 60  # seconds

def get_xrp_prices():
    # Gets both USD and ZAR from CoinGecko
    url = "https://api.coingecko.com/api/v3/simple/price?ids=ripple&vs_currencies=usd,zar"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()["ripple"]
    return data["usd"], data["zar"]

def send_price(usd, zar):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    data = {
        "embeds": [{
            "title": "XRP Live Price",
            "color": 0x00ff9f,
            "fields": [
                {"name": "🇺🇸 USD", "value": f"**${usd:,.4f}**", "inline": True},
                {"name": "🇿🇦 ZAR", "value": f"**R{zar:,.2f}**", "inline": True},
                {"name": "Time", "value": now, "inline": False}
            ],
            "footer": {"text": "Source: CoinGecko"}
        }]
    }
    
    requests.post(DISCORD_WEBHOOK, json=data)

print("XRP Price Monitor started (USD + ZAR)...")
print("Press Ctrl + C to stop\n")

while True:
    try:
        usd, zar = get_xrp_prices()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] XRP = ${usd:.4f} | R{zar:.2f}")
        send_price(usd, zar)
    except Exception as e:
        print("Error:", e)
    
    time.sleep(CHECK_EVERY)