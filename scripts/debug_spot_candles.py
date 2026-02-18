import requests
import time

BASE_URL = "https://api.hyperliquid.xyz/info"

def get_candles(coin, interval="1h", limit=1):
    end_time = int(time.time() * 1000)
    start_time = end_time - (limit * 3600 * 1000)
    req = {
        "coin": coin,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time
    }
    resp = requests.post(BASE_URL, json={"type": "candleSnapshot", "req": req})
    return resp.json()

res = get_candles("@276")
if res and len(res) > 0:
    print(f"Candle structure (first item): {res[0]}")
    print(f"Number of fields: {len(res[0]) if isinstance(res[0], list) else 'Dict keys'}")
else:
    print("No data for @276")
