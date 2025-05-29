# crypto_prices.py
import requests
import json
from datetime import datetime

# CoinGecko's simple price endpoint usually doesn't need an API key.

def get_crypto_prices(symbols_list: list):
    """
    Fetches the current price and timestamp for a list of cryptocurrencies from CoinGecko.
    Symbols are expected in 'COIN/VS_CURRENCY' format, e.g., 'BTC/USD'.
    """
    # Mapping dashboard symbols to CoinGecko IDs and vs_currencies
    coin_map = {
        "BTC/USD": {"id": "bitcoin", "vs_currency": "usd"},
        "ETH/USD": {"id": "ethereum", "vs_currency": "usd"},
        "SOL/USD": {"id": "solana", "vs_currency": "usd"},
        "XRP/USD": {"id": "ripple", "vs_currency": "usd"},
        "ADA/USD": {"id": "cardano", "vs_currency": "usd"},
        "DOGE/USD": {"id": "dogecoin", "vs_currency": "usd"},
        "RVN/USD": {"id": "ravencoin", "vs_currency": "usd"},
        # Add more if needed. Find CoinGecko ID from their API docs or URL (e.g., coingecko.com/en/coins/bitcoin -> ID is 'bitcoin')
    }

    # Filter for valid symbols
    valid_coin_ids = []
    vs_currencies_set = set() # To collect all vs_currencies (should mostly be 'usd')
    
    for symbol in symbols_list:
        if symbol in coin_map:
            valid_coin_ids.append(coin_map[symbol]["id"])
            vs_currencies_set.add(coin_map[symbol]["vs_currency"])
        else:
            print(f"Warning: Symbol {symbol} not configured for CoinGecko lookup. Skipping.")

    if not valid_coin_ids or not vs_currencies_set:
        print("No valid crypto symbols to fetch.")
        return {}

    # Join coin IDs and vs_currencies for the API call
    ids_param = ",".join(valid_coin_ids)
    vs_currencies_param = ",".join(vs_currencies_set)

    COINGECKO_API_URL = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": ids_param,
        "vs_currencies": vs_currencies_param,
        "include_last_updated_at": "true"
    }
    print(f"Attempting to fetch crypto data for {ids_param.upper()} from CoinGecko.")

    all_crypto_data = {}
    try:
        response = requests.get(COINGECKO_API_URL, params=params)
        response.raise_for_status()
        data = response.json()

        print(f"DEBUG: Raw response from CoinGecko: {data}")

        for symbol in symbols_list:
            if symbol not in coin_map:
                continue # Already warned about this symbol

            coin_id = coin_map[symbol]["id"]
            vs_currency = coin_map[symbol]["vs_currency"]

            if coin_id in data and vs_currency in data[coin_id] and 'last_updated_at' in data[coin_id]:
                price = float(data[coin_id][vs_currency])
                timestamp_unix = data[coin_id]['last_updated_at']
                timestamp = datetime.fromtimestamp(timestamp_unix).strftime('%Y-%m-%d %H:%M:%S UTC')
                all_crypto_data[symbol] = {"price": price, "timestamp": timestamp}
            else:
                print(f"No valid data from CoinGecko for {symbol} (ID: {coin_id}). Raw data for this ID: {data.get(coin_id, 'Not found')}")
                
    except requests.exceptions.RequestException as e:
        print(f"Network or API request error for CoinGecko: {type(e).__name__}: {e}")
    except json.JSONDecodeError:
        print(f"Error decoding JSON response from CoinGecko. Response content: {response.text}")
    except Exception as e:
        print(f"An unexpected error occurred in CoinGecko fetching: {type(e).__name__}: {e}")
        
    return all_crypto_data

# Test the function (optional)
if __name__ == "__main__":
    test_symbols = ["BTC/USD", "ETH/USD", "RVN/USD", "NONEXISTENT/USD"]
    prices = get_crypto_prices(test_symbols)
    if prices:
        for symbol, data in prices.items():
            print(f"Current {symbol} Price: {data['price']} as of {data['timestamp']}")
    else:
        print("Failed to get crypto prices.")