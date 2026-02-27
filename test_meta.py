import urllib.request
import json
req = urllib.request.Request(
    'https://api.hyperliquid.xyz/info',
    data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
    headers={'Content-Type': 'application/json'}
)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    universe = data[0].get("universe", [])
    # wait flx:SILVER is NOT in main perp universe.
    # Where does the SDK format prices?
    from hyperliquid.utils.types import float_to_wire
    
