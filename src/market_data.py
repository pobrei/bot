import ccxt
import time
import pandas as pd
import logging
from .config import BINANCE_API_KEY, BINANCE_API_SECRET

logger = logging.getLogger(__name__)

class MarketData:
    def __init__(self):
        self.exchange = ccxt.binance({
            'apiKey': BINANCE_API_KEY,
            'secret': BINANCE_API_SECRET,
            'enableRateLimit': True,
        })
        self.exchange.load_markets()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
        retries = 3
        backoff = 2
        for attempt in range(retries):
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                return df
            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                logger.error(f"Error fetching data (Attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    raise e
