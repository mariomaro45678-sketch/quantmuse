import requests
import json

BASE_URL = "https://api.hyperliquid.xyz"

def get_info(type_str):
    url = f"{BASE_URL}/info"
    payload = {"type": type_str}
    try:
        response = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
        return response.json()
    except Exception as e:
        return {"error": str(e)}

print("\n--- FETCHING SPOT META TOKENS ---")
spot_meta = get_info("spotMeta")

if "tokens" in spot_meta:
    tokens = spot_meta['tokens']
    print(f"Total Spot Tokens: {len(tokens)}")
    
    # Search for ETFs and other potential tickers
    search_terms = ['GOLD', 'XAU', 'SILV', 'XAG', 'TSLA', 'NVDA', 'AAPL', 'SPY', 'GLD', 'SLV', 'IAU', 'USO', 'QQQ', 'IWM']
    for i, token in enumerate(tokens):
        name = token.get('name', '')
        if any(x in name.upper() for x in search_terms):
            print(f"Index {i}: {token} (Universe Name: @{i-1}?)")
            # Spot universe names are usually just the index if it's not a main pair?
            
    # Print first 5 tokens to see structure
    print("First 5 tokens:", tokens[:5])
else:
    print("No 'tokens' key in spotMeta")
    print("Keys:", spot_meta.keys())
