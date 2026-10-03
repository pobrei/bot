import ccxt
import time
import logging
from typing import List

logger = logging.getLogger(__name__)

class TopMoverScreener:
    def __init__(self, exchange: ccxt.binance, cache_duration_seconds: int = 1800):
        self.exchange = exchange
        self.cache_duration = cache_duration_seconds
        self.last_scan_time = 0
        self.cached_symbols = []

    def get_screened_pairs(self, base_basket: List[str], max_extra: int = 2) -> List[str]:
        """
        Dynamically finds the highest-momentum / volume breakout pairs on Binance
        and combines them with the core trading basket.
        """
        now = time.time()
        if self.cached_symbols and (now - self.last_scan_time < self.cache_duration):
            return self.cached_symbols

        try:
            logger.info("Scanning Binance for top breakout volume & momentum pairs...")
            quote_asset = base_basket[0].split('/')[1] if base_basket and '/' in base_basket[0] else 'USDC'
            suffix = f"/{quote_asset}"
            
            tickers = self.exchange.fetch_tickers()
            
            candidates = []
            for symbol, ticker in tickers.items():
                if not symbol.endswith(suffix):
                    continue
                if symbol in base_basket:
                    continue
                # Exclude leveraged / down / up / bear / bull tokens
                if any(bad in symbol for bad in ['UP/', 'DOWN/', 'BULL/', 'BEAR/']):
                    continue
                
                vol = ticker.get('quoteVolume') or 0
                pct = ticker.get('percentage') or 0
                
                # Minimum $50,000 24h quote volume to ensure ample liquidity
                if vol >= 50000 and pct > 0:
                    candidates.append({
                        'symbol': symbol,
                        'change_24h': pct,
                        'volume': vol
                    })

            # Sort by highest 24h percentage gain
            candidates.sort(key=lambda x: x['change_24h'], reverse=True)
            
            top_extras = [c['symbol'] for c in candidates[:max_extra]]
            if top_extras:
                logger.info(f"Top breakout movers discovered: {top_extras}")
                for c in candidates[:max_extra]:
                    logger.info(f"  -> {c['symbol']}: +{c['change_24h']:.2f}% (24h Vol: ${c['volume']:,.0f})")

            # Combine core basket + dynamic movers
            screened = list(base_basket) + top_extras
            self.cached_symbols = screened
            self.last_scan_time = now
            return screened

        except Exception as e:
            logger.error(f"Screener failed to fetch top movers: {e}. Falling back to default basket.")
            return base_basket
