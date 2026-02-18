import requests
import json

BASE_URL = "https://api.hyperliquid.xyz"

def get_spot_meta():
    response = requests.post(f"{BASE_URL}/info", json={"type": "spotMeta"})
    return response.json()

meta = get_spot_meta()
tokens = meta['tokens']
universe = meta['universe']

print(f"Tokens count: {len(tokens)}")
print(f"Universe count: {len(universe)}")

# Find Token ID for GLD, SLV, TSLA
target_tokens = ['GLD', 'SLV', 'TSLA', 'NVDA']
token_indices = {}

for i, t in enumerate(tokens):
    if t['name'] in target_tokens:
        token_indices[t['name']] = i
        print(f"Found {t['name']} at Token Index {i}")

# Find Universe entry containing these tokens
for i, u in enumerate(universe):
    # Universe name is usually u['name']
    u_name = u['name']
    u_tokens = u['tokens'] # List of 2 integers
    
    # Check if this pair involves our targets
    for name, idx in token_indices.items():
        if idx in u_tokens:
            print(f"Match! {name} (Token {idx}) is in Universe '{u_name}' (Index {i}) with tokens {u_tokens}")

