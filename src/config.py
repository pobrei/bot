import os
from dotenv import load_dotenv

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
DRY_RUN = os.getenv("DRY_RUN", "True").lower() in ("true", "1", "t", "yes")
_symbols_str = os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
SYMBOLS = [s.strip() for s in _symbols_str.split(",") if s.strip()]
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
CAPITAL_PER_TRADE = float(os.getenv("TRADE_AMOUNT", os.getenv("CAPITAL_EUR", "5.0")))
CAPITAL_EUR = CAPITAL_PER_TRADE  # Alias for backward compatibility

# Dynamic Trailing Stop & Profit Targets (%)
TRAILING_STOP_ACTIVATION = float(os.getenv("TRAILING_STOP_ACTIVATION", "1.5")) # Activate when profit >= 1.5%
TRAILING_STOP_DISTANCE = float(os.getenv("TRAILING_STOP_DISTANCE", "1.0"))     # Trail 1.0% below peak
HARD_STOP_LOSS = float(os.getenv("HARD_STOP_LOSS", "2.5"))                     # Hard stop at -2.5%
TAKE_PROFIT_TARGET = float(os.getenv("TAKE_PROFIT_TARGET", "5.0"))             # Fixed TP target at +5.0%

# Dynamic Screener
DYNAMIC_SCREENER = os.getenv("DYNAMIC_SCREENER", "True").lower() in ("true", "1", "t", "yes")
