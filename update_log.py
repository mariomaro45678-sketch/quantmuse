import re

log_content = """## [2026-02-21 09:25] 🤖 Antigravity | 🎯 Fixed Margin Constraints and Diagnosed DEX Isolation
- **Changes**: `run_multi_strategy.py` (Fixed missing `notional` variable; plumbed `eval_leverage` down to `submit_entry` and `create_order`), `hyperliquid_executor.py` (Added `math.ceil` rounding to dynamically satisfy margin limits with `update_leverage`).
- **Context**: Solved the `leverage` calculation bug causing "$11 minimum" trades to get rejected on small accounts. Discovered that the remaining "Insufficient margin" error for XAG (`asset=120006`) is because Hyperliquid's `flx` DEX has an **isolated clearinghouse** with exactly $0.00 margin. `xyz` DEX has $61.86, and `main` has $14.77. Since the bot only has an API Agent secret, it cannot transfer USDC programmatically. The user must manually transfer USDC to the `flx` DEX to trade XAG/SILVER.
"""

with open("_PROJECT_LOG.md", "r") as f:
    orig = f.read()

# Insert the new log after the "## 📝 RECENT SESSION LOGS" header
lines = orig.split('\n')
header_idx = -1
for i, line in enumerate(lines):
    if "RECENT SESSION LOGS" in line:
        header_idx = i
        break

if header_idx != -1:
    lines.insert(header_idx + 1, log_content)
    with open("_PROJECT_LOG.md", "w") as f:
        f.write('\n'.join(lines))
else:
    with open("_PROJECT_LOG.md", "a") as f:
        f.write("\n" + log_content)
