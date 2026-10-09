#!/usr/bin/env python3

import os
import json
import requests
from typing import Optional

def get_symbol_info(symbol: str, category: str = "linear", testnet: bool = False):
    """
    Fetches market instruments information for a given symbol.
    """
    api_key = os.environ.get('BYBIT_API_KEY')
    api_secret = os.environ.get('BYBIT_API_SECRET')
    base_url = os.environ.get("BYBIT_BASE_URL", "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com")

    # Prepare proxies from environment variables
    proxies = {}
    http_proxy = os.environ.get('HTTP_PROXY') or os.environ.get('http_proxy')
    https_proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if http_proxy:
        proxies['http'] = http_proxy
    if https_proxy:
        proxies['https'] = https_proxy
    
    # Public endpoint, no signature needed usually, but include for consistency
    # params = {"symbol": symbol, "category": category}
    # timestamp = str(int(time.time() * 1000))
    # recv_window = "5000"
    # param_str = timestamp + api_key + recv_window + "" # No query string for this endpoint
    # signature = hmac.new(api_secret.encode('utf-8'), param_str.encode('utf-8'), hashlib.sha256).hexdigest()
    # headers = {
    #     "X-BAPI-API-KEY": api_key,
    #     "X-BAPI-TIMESTAMP": timestamp,
    #     "X-BAPI-SIGN": signature,
    #     "X-BAPI-RECV-WINDOW": recv_window,
    #     "Content-Type": "application/json"
    # }
    
    url = f"{base_url}/v5/market/instruments-info"
    
    # Parameters for the instruments-info endpoint
    # The documentation suggests category is required, symbol is optional for listing all.
    # To get specific symbol info, we pass symbol and category.
    params = {
        "category": category,
        "symbol": symbol
    }

    try:
        print(f"Fetching info for symbol: {symbol} (Category: {category}) from {url}")
        response = requests.get(
            url,
            params=params,
            proxies=proxies if proxies else None,
            timeout=30
        )
        
        try:
            response_data = response.json()
        except json.JSONDecodeError:
            print(f"❌ API Response Error!")
            print(f"Status Code: {response.status_code}")
            print(f"Response Text: {response.text}")
            print(f\"\"\"
            This often happens if the API is blocked by CloudFront or a firewall.
            \"\"\")
            return

        if response.status_code == 200 and response_data.get("retCode") == 0:
            result = response_data.get("result", {})
            instruments = result.get("list", [])
            
            if instruments:
                instrument_info = instruments[0] # Get info for the specific symbol requested
                
                print(f"
--- Symbol Info for {symbol} ---")
                print(f"Symbol: {instrument_info.get('symbol', 'N/A')}")
                print(f"Category: {instrument_info.get('category', 'N/A')}")
                print(f"Base Coin: {instrument_info.get('baseCoin', 'N/A')}")
                print(f"Quote Coin: {instrument_info.get('quoteCoin', 'N/A')}")
                
                # Lot Size Filter
                lot_size_filter = instrument_info.get('lotSizeFilter')
                if lot_size_filter:
                    print(f"Lot Size Filter:")
                    print(f"  - Max Order Qty: {lot_size_filter.get('maxOrderQty', 'N/A')}")
                    print(f"  - Min Order Qty: {lot_size_filter.get('minOrderQty', 'N/A')}")
                    print(f"  - Qty Step: {lot_size_filter.get('qtyStep', 'N/A')}")
                
                # Price Filter
                price_filter = instrument_info.get('priceFilter')
                if price_filter:
                    print(f"Price Filter:")
                    print(f"  - Tick Size: {price_filter.get('tickSize', 'N/A')}")
                    print(f"  - Max Price: {price_filter.get('maxPrice', 'N/A')}")
                    print(f"  - Min Price: {price_filter.get('minPrice', 'N/A')}")

                # Value Filter
                value_filter = instrument_info.get('valueFilter')
                if value_filter:
                    print(f"Value Filter:")
                    print(f"  - Min Order Value: {value_filter.get('minOrderValue', 'N/A')}")
                    print(f"  - Max Order Value: {value_filter.get('maxOrderValue', 'N/A')}")

                print("
Full Response:")
                print(json.dumps(response_data, indent=2))
            else:
                print(f"No instrument info found for symbol {symbol}.")
        else:
            print(f"❌ API Request Failed!")
            print(f"Status Code: {response.status_code}")
            print(f"Error Code: {response_data.get('retCode', 'N/A')}")
            print(f"Error Message: {response_data.get('retMsg', 'Unknown error')}")
            print("
Full Response:")
            print(json.dumps(response_data, indent=2))
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Network Error: {str(e)}")
    except Exception as e:
        print(f"❌ Unexpected Error: {str(e)}")

if __name__ == "__main__":
    symbol_to_check = "TRUMPUSDT"
    category_to_check = "linear" # Assuming linear as per previous attempts
    
    # Ensure Tor proxy is set if available
    if not os.environ.get('HTTPS_PROXY'):
        print("Warning: HTTPS_PROXY environment variable not set. Using direct connection (may fail due to geo-restrictions).")

    get_symbol_info(symbol=symbol_to_check, category=category_to_check, testnet=False)
