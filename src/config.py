import os
from dotenv import load_dotenv

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
DRY_RUN = os.getenv("DRY_RUN", "True").lower() in ("true", "1", "t", "yes")
_symbols_str = os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,AVAX/USDT,NEAR/USDT")
SYMBOLS = [s.strip() for s in _symbols_str.split(",") if s.strip()]
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
CAPITAL_PER_TRADE = float(os.getenv("TRADE_AMOUNT", os.getenv("CAPITAL_EUR", "5.0")))
CAPITAL_EUR = CAPITAL_PER_TRADE  # Alias for backward compatibility

# Dynamic Trailing Stop & Profit Targets (%)
TRAILING_STOP_ACTIVATION = float(os.getenv("TRAILING_STOP_ACTIVATION", "1.5")) # Activate when profit >= 1.5%
TRAILING_STOP_DISTANCE = float(os.getenv("TRAILING_STOP_DISTANCE", "1.0"))     # Trail 1.0% below peak
HARD_STOP_LOSS = float(os.getenv("HARD_STOP_LOSS", "2.5"))                     # Hard stop at -2.5%
TAKE_PROFIT_TARGET = float(os.getenv("TAKE_PROFIT_TARGET", "5.0"))             # Fixed TP target at +5.0%

# Dynamic Screener (defaults to False to trade strictly curated high-conviction assets)
DYNAMIC_SCREENER = os.getenv("DYNAMIC_SCREENER", "False").lower() in ("true", "1", "t", "yes")

def get_default_min_atr_ratio(tf: str = "15m") -> float:
    """Returns realistic minimum ATR hurdle scaled by candle timeframe."""
    if tf in ("1m", "3m", "5m"):
        return 0.0020  # 0.20% per candle
    elif tf in ("15m", "30m"):
        return 0.0030  # 0.30% per candle (~4x round-trip Binance fee of 0.075%)
    elif tf in ("1h", "2h"):
        return 0.0075  # 0.75% per candle
    else:
        return 0.0150  # 1.50% per candle (standard for 4h/1D)

# Volatility Filter (minimum ATR / close ratio, scaled to active timeframe)
_default_atr = str(get_default_min_atr_ratio(TIMEFRAME))
MIN_ATR_RATIO = float(os.getenv("MIN_ATR_RATIO", _default_atr))

# Revolut Signal Bridge (Gemini AI Sentiment & Multi-Timeframe Signals)
USE_REVOLUT_SIGNALS = os.getenv("USE_REVOLUT_SIGNALS", "True").lower() in ("true", "1", "t", "yes")
REVOLUT_API_URL = os.getenv("REVOLUT_API_URL", "http://127.0.0.1:8000")
REVOLUT_DB_PATH = os.getenv("REVOLUT_DB_PATH", "/Users/filippsh/dev/revolutbot/revolut_signals.db")
REVOLUT_MIN_SCORE = float(os.getenv("REVOLUT_MIN_SCORE", "0.35"))

