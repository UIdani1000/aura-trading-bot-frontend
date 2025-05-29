import os
import time
import json
import re # Import regex module
from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin # Ensure cross_origin is imported
import google.generativeai as genai
from collections import deque
from datetime import datetime # Make sure datetime is imported
import threading
import requests
import logging # Ensure logging is imported
import config # Ensure config is imported at the top
import sqlite3 # Import sqlite3 for database operations

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

# --- Database setup ---
DATABASE = 'trades.db' # This file will be created in your app's root directory on Render

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row # This makes rows behave like dictionaries
    return conn

def init_db():
    with get_db() as db:
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                profit_loss REAL NOT NULL,
                trade_type TEXT NOT NULL, -- e.g., 'buy' or 'sell'
                timestamp TEXT NOT NULL -- ISO format string
            )
        ''')
        db.commit()

# Initialize the database when the app starts
# This ensures the 'trades.db' file and 'trades' table are created
# when the Flask app context is available (e.g., on startup).
with app.app_context():
    init_db()
# --- End Database setup ---


# --- Gemini API Configuration ---
gemini_model = None # Initialize as None
chat = None # Initialize chat as None

def configure_gemini():
    global gemini_model, chat # Declare global to modify them
    try:
        # Check if config.py is imported and GEMINI_API_KEY is accessible
        if not hasattr(config, 'GEMINI_API_KEY'):
            logging.error("Error: 'config' module does not have 'GEMINI_API_KEY' attribute.")
            raise AttributeError("GEMINI_API_KEY not found in config.py.")
        
        if not config.GEMINI_API_KEY:
            logging.error("Error: GEMINI_API_KEY is empty or None in config.py.")
            raise ValueError("GEMINI_API_KEY is not set in config.py.")
        
        # Log that we are attempting to configure the API
        logging.info(f"Attempting to configure Gemini API. Key status: {'Set' if config.GEMINI_API_KEY else 'Not Set'}")
        
        genai.configure(api_key=config.GEMINI_API_KEY)
        logging.info("Gemini API configured successfully using config.py.")
        
        # Using the recommended model gemini-1.5-flash
        gemini_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash', # Changed back to gemini-1.5-flash
            generation_config={
                "temperature": 0.7,
                "top_p": 1,
                "top_k": 1,
                "max_output_tokens": 2048,
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ]
        )
        chat = gemini_model.start_chat(history=[])
        return gemini_model
    except Exception as e:
        logging.error(f"Failed to configure Gemini API during startup: {e}")
        gemini_model = None # Ensure model is None on failure
        chat = None
        return None

# Call configuration on startup
configure_gemini()

# Chat history storage (using deque for fixed size, e.g., last 10 messages)
chat_history = deque(maxlen=10)

# --- CoinGecko Integration ---
COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
MARKET_DATA_ENDPOINT = f"{COINGECKO_API_BASE}/simple/price"
SUPPORTED_CRYPTOS = {
    "BTC/USD": "bitcoin",
    "ETH/USD": "ethereum",
    "SOL/USD": "solana",
    "XRP/USD": "ripple",
    "ADA/USD": "cardano",
    "DOGE/USD": "dogecoin",
    "RVN/USD": "ravencoin" # Assuming Ravencoin is supported on CoinGecko
}
COINGECKO_IDS = ",".join(SUPPORTED_CRYPTOS.values())

# Cache for market prices with a timestamp
market_prices_cache = {}
CACHE_DURATION = 10 # seconds

# Rate limiting for CoinGecko API to avoid hitting limits (e.g., 50 calls/min)
# Using a deque to store timestamps of last N calls
api_call_timestamps = deque()
MAX_CALLS_PER_MINUTE = 45 # Slightly below 50 to be safe
ONE_MINUTE = 60 # seconds

def get_market_prices_coingecko():
    current_time = time.time()

    # Clean up old timestamps
    while api_call_timestamps and api_call_timestamps[0] < current_time - ONE_MINUTE:
        api_call_timestamps.popleft()

    # If we're hitting the limit, wait
    if len(api_call_timestamps) >= MAX_CALLS_PER_MINUTE:
        time_to_wait = ONE_MINUTE - (current_time - api_call_timestamps[0])
        if time_to_wait > 0:
            print(f"CoinGecko API rate limit approaching. Waiting for {time_to_wait:.2f} seconds.")
            time.sleep(time_to_wait)
            current_time = time.time() # Update current time after waiting

    # Check cache first
    if market_prices_cache and (current_time - market_prices_cache.get('timestamp', 0) < CACHE_DURATION):
        return market_prices_cache['data']

    try:
        params = {
            "ids": COINGECKO_IDS,
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        }
        response = requests.get(MARKET_DATA_ENDPOINT, params=params)
        response.raise_for_status() # Raise an exception for HTTP errors
        data = response.json()

        # Add timestamp to API call history
        api_call_timestamps.append(time.time())

        # Process data into the desired "PAIR/USD" format
        processed_data = {}
        for pair_key, coingecko_id in SUPPORTED_CRYPTOS.items():
            if coingecko_id in data and 'usd' in data[coingecko_id]:
                price = data[coingecko_id]['usd']
                change_24hr = data[coingecko_id].get('usd_24h_change', 0)

                # Calculate absolute change based on price and percentage change
                absolute_change = (price * change_24hr) / 100 

                processed_data[pair_key] = {
                    "price": price,
                    "change": absolute_change, # This is the USD change over 24h
                    "percent_change": change_24hr
                }
            else:
                print(f"Warning: Data for {pair_key} ({coingecko_id}) not found in CoinGecko response.")
                processed_data[pair_key] = {
                    "price": 0.0,
                    "change": 0.0,
                    "percent_change": 0.0
                }

        market_prices_cache['data'] = processed_data
        market_prices_cache['timestamp'] = current_time
        return processed_data

    except requests.exceptions.RequestException as e:
        print(f"Error fetching market prices from CoinGecko: {e}")
        # Return last good data if available, or empty dict
        return market_prices_cache.get('data', {})
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from CoinGecko: {e}")
        return market_prices_cache.get('data', {})

# Pre-fetch and cache prices on startup in a separate thread
def pre_fetch_prices():
    with app.app_context(): # Ensure this runs within the Flask app context
        get_market_prices_coingecko()

threading.Thread(target=pre_fetch_prices).start()

@app.route('/')
def hello_world():
    return 'Flask Backend is Running!'

@app.route('/chat', methods=['POST'])
def chat_with_gemini(): # Renamed to avoid conflict with global 'chat' variable
    user_message = request.json.get('message')
    user_name = request.json.get('userName', 'Trader')
    ai_name = request.json.get('aiName', 'Aura')

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    if not gemini_model: # Check if model is initialized
        logging.error("Gemini model not initialized for chat. API key might be invalid or model access failed.")
        return jsonify({"error": "AI brain not available for chat. Please check API key configuration."}), 500

    try:
        # System instruction to define AI's persona
        system_instruction = (
            f"You are {ai_name}, an expert AI trading assistant. "
            f"Your responses should be helpful, insightful, and concise, focusing on providing actionable trading advice and market analysis. "
            f"Use a professional yet approachable tone. Incorporate real-time market data when available. "
            f"Always consider risk management. Never give financial advice explicitly stating 'I advise you to buy/sell', but rather present analysis. "
            f"Address the user as {user_name}. Keep responses under 150 words."
        )

        # Ensure the chat object is ready for the current conversation
        if not chat.history:
            chat.send_message(system_instruction)

        response = chat.send_message(user_message)
        
        ai_response = response.text

        return jsonify({"response": ai_response})
    except Exception as e:
        logging.error(f"Error in chat: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/all_market_prices', methods=['GET'])
def all_market_prices():
    prices = get_market_prices_coingecko()
    if not prices:
        return jsonify({"error": "Could not fetch market prices"}), 500
    return jsonify(prices)

@app.route('/generate_analysis', methods=['POST'])
def generate_analysis():
    if not gemini_model: # Check if model is initialized
        logging.error("Gemini model not initialized for analysis. API key might be invalid or model access failed.")
        return jsonify({"error": "AI brain not available for analysis."}), 500

    data = request.json
    pair = data.get('pair')
    timeframes = data.get('timeframes', []) # List of timeframes
    indicators = data.get('indicators', []) # List of indicators
    trade_type = data.get('trade_type')
    balance_range = data.get('balance_range')
    leverage = data.get('leverage')
    current_price = data.get('current_price_for_pair') # Get the current price passed from frontend

    if not all([pair, timeframes, indicators, trade_type, balance_range, leverage, current_price is not None]):
        missing_params = []
        if not pair: missing_params.append('pair')
        if not timeframes: missing_params.append('timeframes')
        if not indicators: missing_params.append('indicators')
        if not trade_type: missing_params.append('trade_type')
        if not balance_range: missing_params.append('balance_range')
        if not leverage: missing_params.append('leverage')
        if current_price is None: missing_params.append('current_price_for_pair')
        return jsonify({"error": f"Missing analysis parameters: {', '.join(missing_params)}."}), 400

    # Ensure current_price is a float for calculations later
    try:
        current_price = float(current_price)
    except ValueError:
        return jsonify({"error": "Invalid current price format"}), 400

    timeframe_str = ", ".join(timeframes)
    indicators_str = ", ".join(indicators)

    # Detailed prompt engineering for structured output
    analysis_prompt = (
        f"You are Aura, an advanced AI trading assistant. "
        f"Perform a comprehensive technical analysis for **{pair}** based on the following parameters:\n"
        f"- **Timeframes:** {timeframe_str}\n"
        f"- **Technical Indicators:** {indicators_str}\n"
        f"- **Intended Trade Type:** {trade_type}\n"
        f"- **Trader's Balance Range:** {balance_range}\n"
        f"- **Leverage:** {leverage}\n"
        f"- **Current Market Price of {pair}:** {current_price}\n\n"
        f"Your analysis should provide a clear trading signal (BUY, SELL, or NEUTRAL), a confidence level, and precise entry, take profit (TP1, TP2, TP3), and stop loss (SL) prices. "
        f"Additionally, provide a Risk:Reward (R:R) ratio. "
        f"**Output your analysis in two distinct parts.**\n"
        f"**PART 1: JSON Data**\n"
        f"Provide a JSON object containing the numerical recommendations. Format all price values (entry, TP, SL) to 4 decimal places for precision. Ensure the R:R ratio is a string in '1:X.X' format. Ensure confidence is a string like 'XX%'.\n"
        f"```json\n"
        f"{{\n"
        f"  \"signal\": \"<signal_type>\",\n"
        f"  \"confidence\": \"<percentage>%\",\n"
        f"  \"entry\": <float_price>,\n"
        f"  \"tp1\": <float_price>,\n"
        f"  \"tp2\": <float_price>,\n"
        f"  \"tp3\": <float_price>,\n"
        f"  \"sl\": <float_price>,\n"
        f"  \"rr_ratio\": \"1:<ratio>\"\n"
        f"}}\n"
        f"```\n\n"
        f"**PART 2: Detailed Textual Analysis**\n"
        f"After the JSON, provide a detailed textual explanation of your analysis. **Do NOT repeat the JSON data or its headings in this part.** Explain why you arrived at the signal, what insights you gathered from the specified timeframes and indicators, how the trade type, balance, and leverage factor into your recommendations, and what risk management considerations are important. Make sure this part is conversational and helpful, specifically referencing the numerical values you provided in the JSON. Keep this textual analysis under 250 words."
    )

    try:
        # Start a new chat session for analysis to keep it clean from previous conversational history
        convo = gemini_model.start_chat(history=[]) # Use gemini_model here
        response = convo.send_message(analysis_prompt)
        ai_full_response = response.text

        analysis_data = {}
        ai_analysis_text = "Analysis not available."

        # Use regex to find the JSON block and capture the text before/after it
        # This regex tries to capture the JSON block and then the remaining text
        json_pattern = r'(?s)(.*?)```json\n({.*?})\n```(.*)'
        json_match = re.search(json_pattern, ai_full_response)

        if json_match:
            pre_json_text = json_match.group(1).strip()
            json_str = json_match.group(2)
            post_json_text = json_match.group(3).strip()

            try:
                analysis_data = json.loads(json_str)
                
                # Combine pre and post JSON text for the final textual analysis
                ai_analysis_text = post_json_text # Prioritize text after JSON
                if not ai_analysis_text and pre_json_text: # If no text after, use text before
                    ai_analysis_text = pre_json_text
                if not ai_analysis_text: # Fallback if both are empty
                    ai_analysis_text = "A detailed textual analysis could not be generated."

                # Clean up any remaining "PART 2" or similar headers that might be in the text
                ai_analysis_text = re.sub(r'^\s*(\*\*PART 2: Detailed Textual Analysis\*\*|\*\*Detailed Textual Analysis\*\*|\*\*PART 2\*\*)\s*', '', ai_analysis_text, flags=re.IGNORECASE | re.MULTILINE).strip()


                # Basic validation for essential keys in analysis_data
                required_keys = ["signal", "confidence", "entry", "tp1", "tp2", "tp3", "sl", "rr_ratio"]
                if not all(key in analysis_data for key in required_keys):
                    logging.warning(f"Missing required keys in AI-generated JSON: {analysis_data}. Falling back to defaults.")
                    # Fallback to default values if keys are missing
                    analysis_data = {
                        "signal": "NEUTRAL",
                        "confidence": "N/A",
                        "entry": round(current_price, 4),
                        "tp1": round(current_price * 1.001, 4),
                        "tp2": round(current_price * 1.002, 4),
                        "tp3": round(current_price * 1.003, 4),
                        "sl": round(current_price * 0.999, 4),
                        "rr_ratio": "1:1.0"
                    }
                    ai_analysis_text = "A detailed textual analysis could not be generated as the AI did not provide a complete structured output. " + ai_analysis_text


            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON from Gemini response: {e}")
                # Fallback to default values if JSON is malformed
                analysis_data = {
                    "signal": "NEUTRAL",
                    "confidence": "N/A",
                    "entry": round(current_price, 4),
                    "tp1": round(current_price * 1.001, 4),
                    "tp2": round(current_price * 1.002, 4),
                    "tp3": round(current_price * 1.003, 4),
                    "sl": round(current_price * 0.999, 4),
                    "rr_ratio": "1:1.0"
                }
                ai_analysis_text = "The AI generated an analysis, but its structured data was unreadable. Please try again. " + ai_full_response
        else:
            # If no JSON block found, try to use the whole response as text and fallback numerical values
            ai_analysis_text = ai_full_response.strip()
            analysis_data = {
                "signal": "NEUTRAL",
                "confidence": "N/A",
                "entry": round(current_price, 4),
                "tp1": round(current_price * 1.001, 4),
                "tp2": round(current_price * 1.002, 4),
                "tp3": round(current_price * 1.003, 4),
                "sl": round(current_price * 0.999, 4),
                "rr_ratio": "1:1.0"
            }
            if not ai_analysis_text:
                ai_analysis_text = "The AI could not generate a full analysis. Please try again."
            else:
                ai_analysis_text = "The AI generated a textual analysis but no structured data. " + ai_analysis_text

        # Return both the structured data and the textual analysis
        return jsonify({
            "analysis_data": analysis_data,
            "ai_analysis_text": ai_analysis_text
        })

    except Exception as e:
        logging.error(f"Error generating analysis: {e}")
        return jsonify({"error": str(e)}), 500

# Endpoint for logging trades (your existing one)
@app.route('/log_trade', methods=['POST'])
@cross_origin()
def log_trade():
    data = request.json
    if not data:
        return jsonify({'error': 'Invalid JSON data'}), 400

    required_fields = ['pair', 'entry_price', 'exit_price', 'profit_loss', 'trade_type']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400

    try:
        pair = data['pair']
        entry_price = float(data['entry_price'])
        exit_price = float(data['exit_price'])
        profit_loss = float(data['profit_loss'])
        trade_type = data['trade_type']
        timestamp = datetime.now().isoformat()

        with get_db() as db:
            cursor = db.cursor()
            cursor.execute('''
                INSERT INTO trades (pair, entry_price, exit_price, profit_loss, trade_type, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (pair, entry_price, exit_price, profit_loss, trade_type, timestamp))
            db.commit()

        return jsonify({'message': 'Trade logged successfully!'}), 201
    except ValueError:
        return jsonify({'error': 'Invalid number format for prices or profit/loss'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# NEW ENDPOINT: Get all trades for analytics
@app.route('/get_trades', methods=['GET'])
@cross_origin()
def get_trades():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, pair, entry_price, exit_price, profit_loss, trade_type, timestamp FROM trades")
        trades = cursor.fetchall()
        # Convert Row objects to dictionaries for JSON serialization
        trades_list = []
        for trade in trades:
            trades_list.append(dict(trade))
        return jsonify(trades_list), 200
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# This part is usually for local development
if __name__ == '__main__':
    # Get port from environment variable or default to 5000
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False) # use_reloader=False if running with threading