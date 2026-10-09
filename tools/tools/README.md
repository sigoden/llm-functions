## Bybit Tools

This directory contains three Bash utilities for interacting with the Bybit API:

- **bybit_fee_rate.sh** – fetches taker/maker fee rates for a given category.
- **bybit_closed_pnl.sh** – retrieves closed‑PNL data for a symbol over a time window.
- **bybit_transaction_log.sh** – pulls the transaction log (fees, funding, cash flow, etc.).

All scripts read a `.env` file for API credentials and support optional proxying via `proxychains4`. They write a JSON summary to the `$LLM_OUTPUT` variable for downstream consumption.

**Usage example**

```bash
./tools/bybit_fee_rate.sh --category spot
./tools/bybit_closed_pnl.sh --category spot --symbol BTCUSD
./tools/bybit_transaction_log.sh --action fetch
```

Feel free to modify the scripts to suit your workflow.
