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

print("--- FETCHING PERP META ---")
meta = get_info("meta")
if "universe" in meta:
    print(f"Perp Universe Size: {len(meta['universe'])}")
    xau = [x for x in meta['universe'] if 'XAU' in x['name']]
    print(f"XAU in Perps: {xau}")
    
print("\n--- FETCHING SPOT META ---")
spot_meta = get_info("spotMeta")
if "universe" in spot_meta:
    print(f"Spot Universe Size: {len(spot_meta['universe'])}")
    xau_spot = [x for x in spot_meta['universe'] if 'XAU' in x['name'] or 'Gold' in x.get('name', '')]
    print(f"XAU in Spot: {xau_spot}")
    xag_spot = [x for x in spot_meta['universe'] if 'XAG' in x['name']]
    print(f"XAG in Spot: {xag_spot}")

print("\n--- FETCHING PERP META AND ASSET CXTS ---")
meta_cxts = get_info("metaAndAssetCtxs")
if isinstance(meta_cxts, list) and len(meta_cxts) > 0:
    # meta_cxts[0] is meta, meta_cxts[1] is ctxs
    universe = meta_cxts[0]['universe']
    print(f"Searching Universe for HIP-3 assets...")
    for i, asset in enumerate(universe):
        if asset['name'] in ['kPEPE', 'kSHIB']: # HIP-1 examples
            print(f"Found HIP-1 asset: {asset['name']}")
        if asset['name'] in ['XAU', 'XAG', 'HPOS']:
             print(f"Found Potential HIP-3/Other: {asset['name']}")

# Check if there's a specific "HIP-3" or other info type?
# Trying valid leverage
