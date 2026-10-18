import os
import re
import time
import logging
import sqlite3
from typing import Dict, List, Optional, Tuple, Any
import requests

from .config import (
    USE_REVOLUT_SIGNALS,
    REVOLUT_API_URL,
    REVOLUT_DB_PATH,
    REVOLUT_MIN_SCORE
)

logger = logging.getLogger(__name__)

class RevolutSignalBridge:
    """
    Bridges the Revolut Market Intelligence Bot (Gemini AI Sentiment + Multi-Timeframe Technics)
    with the Binance Autonomous Execution Engine.
    """
    def __init__(
        self, 
        exchange=None, 
        api_url: str = REVOLUT_API_URL, 
        db_path: str = REVOLUT_DB_PATH,
        cache_ttl: float = 30.0
    ):
        self.exchange = exchange
        self.api_url = api_url.rstrip("/")
        self.db_path = db_path
        self.cache_ttl = cache_ttl
        self._last_fetch_time = 0.0
        self._cached_intel: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def clean_base_symbol(ticker: str) -> str:
        """
        Cleans ticker names from Revolut (e.g. 'STX4847-USD' -> 'STX', 'SOL-USD' -> 'SOL').
        """
        if not ticker:
            return ""
        # If ticker already contains slash, take the base
        if "/" in ticker:
            return ticker.split("/")[0].upper()
        # Strip quote if -USD or -USDT
        raw_base = ticker.split("-")[0].upper()
        # Remove trailing coinmarketcap / revolut ID numbers: e.g. STX4847 -> STX, SUI20947 -> SUI
        cleaned = re.sub(r"\d+$", "", raw_base)
        return cleaned

    def map_to_binance(self, ticker: str, preferred_quote: str = "USDC") -> Optional[str]:
        """
        Maps a Revolut ticker to an active Binance spot market symbol.
        Prefers `preferred_quote` (default USDC), falling back to USDT if available.
        """
        base = self.clean_base_symbol(ticker)
        if not base or base.upper() in ("USDC", "USDT", "USD", "EUR", "BUSD", "FDUSD", "DAI", "TUSD"):
            return None

        # If exchange markets are loaded, verify availability
        if self.exchange and hasattr(self.exchange, "markets") and self.exchange.markets:
            cand_pref = f"{base}/{preferred_quote.upper()}"
            if cand_pref in self.exchange.markets and self.exchange.markets[cand_pref].get("spot", True):
                return cand_pref
            
            # Fallback to alternative common quote
            alt_quote = "USDT" if preferred_quote.upper() != "USDT" else "USDC"
            cand_alt = f"{base}/{alt_quote}"
            if cand_alt in self.exchange.markets and self.exchange.markets[cand_alt].get("spot", True):
                return cand_alt
            return None
        
        # Default mapping without exchange connection
        return f"{base}/{preferred_quote.upper()}"

    def fetch_snapshots(self) -> List[Tuple]:
        """
        Fetches latest market snapshots. Tries HTTP API first, falls back to direct SQLite read.
        """
        # 1. Try HTTP API
        try:
            resp = requests.get(f"{self.api_url}/api/snapshots", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug(f"Revolut API unreachable at {self.api_url} ({e}); falling back to SQLite.")

        # 2. Fallback to direct SQLite read
        if os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                cur = conn.cursor()
                cur.execute("""
                    SELECT symbol, price, action, composite_score, tech_score, sentiment_score, summary, updated_at 
                    FROM market_snapshots
                    ORDER BY updated_at DESC
                """)
                rows = cur.fetchall()
                conn.close()
                return rows
            except Exception as e:
                logger.error(f"Error reading Revolut SQLite DB at {self.db_path}: {e}")

        return []

    def get_all_intel(self, preferred_quote: str = "USDC", force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        Returns structured intelligence for all active Binance crypto pairs.
        Cached for `cache_ttl` seconds to avoid overhead.
        Ensures the newest snapshot for each asset is retained.
        """
        now = time.time()
        if not force_refresh and self._cached_intel and (now - self._last_fetch_time < self.cache_ttl):
            return self._cached_intel

        snapshots = self.fetch_snapshots()
        intel_map = {}

        for item in snapshots:
            # Handle tuple format from sqlite or list format from json
            if isinstance(item, (list, tuple)) and len(item) >= 8:
                ticker = item[0]
                price = float(item[1] or 0.0)
                action = str(item[2] or "HOLD").upper()
                composite_score = float(item[3] or 0.0)
                tech_score = float(item[4] or 0.0)
                sentiment_score = float(item[5] or 0.0)
                summary = str(item[6] or "")
                updated_at = float(item[7] or 0.0)
            else:
                continue

            binance_symbol = self.map_to_binance(ticker, preferred_quote)
            if not binance_symbol:
                continue

            # Only retain crypto pairs (skip equity tickers like AAPL, MSFT)
            # Crypto tickers on Revolut contain '-USD' or '/'
            if ("-USD" not in ticker) and ("/" not in ticker):
                continue

            # If symbol already present, keep the newer snapshot
            if binance_symbol in intel_map:
                if updated_at <= intel_map[binance_symbol].get("updated_at", 0.0):
                    continue

            intel_map[binance_symbol] = {
                "raw_ticker": ticker,
                "binance_symbol": binance_symbol,
                "price": price,
                "action": action,
                "composite_score": composite_score,
                "tech_score": tech_score,
                "sentiment_score": sentiment_score,
                "summary": summary,
                "updated_at": updated_at
            }

        self._cached_intel = intel_map
        self._last_fetch_time = now
        return intel_map

    def fetch_active_trades(self) -> List[Tuple]:
        """
        Fetches active trades from Revolut bot (tries HTTP API /api/trades first, then SQLite).
        """
        try:
            resp = requests.get(f"{self.api_url}/api/trades", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return [t for t in data if isinstance(t, (list, tuple)) and len(t) >= 8 and t[7] in ("OPEN", "TARGET_1_HIT")]
        except Exception:
            pass

        if os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, symbol, action, entry_price, target_1, target_2, stop_loss, status, created_at, updated_at
                    FROM active_trades
                    WHERE status IN ('OPEN', 'TARGET_1_HIT')
                """)
                rows = cur.fetchall()
                conn.close()
                return rows
            except Exception as e:
                logger.error(f"Error reading Revolut active_trades from SQLite: {e}")
        return []

    def get_candidates(
        self, 
        min_score: float = REVOLUT_MIN_SCORE, 
        preferred_quote: str = "USDC",
        max_candidates: int = 5,
        max_age_seconds: float = 43200.0
    ) -> List[Dict[str, Any]]:
        """
        Returns top BUY candidate crypto pairs scored above `min_score`.
        Incorporates both high-scoring live snapshots and active swing BUY trades.
        Higher-timeframe AI sentiment is retained for up to 12 hours (43200s).
        """
        if not USE_REVOLUT_SIGNALS:
            return []

        intel = self.get_all_intel(preferred_quote)
        candidates = []
        seen_symbols = set()

        now = time.time()
        max_age = max_age_seconds

        # 1. Ingest any confirmed BUY swing trades from Revolut active_trades
        active_trades = self.fetch_active_trades()
        for t in active_trades:
            ticker = t[1]
            action = t[2]
            trade_time = float(t[9] or now)
            if action == "BUY" and (now - trade_time <= max_age):
                bin_sym = self.map_to_binance(ticker, preferred_quote)
                if bin_sym:
                    seen_symbols.add(bin_sym)
                    candidates.append({
                        "raw_ticker": ticker,
                        "binance_symbol": bin_sym,
                        "price": float(t[3] or 0.0),
                        "action": "BUY",
                        "composite_score": 1.0, # Maximum conviction
                        "tech_score": 1.0,
                        "sentiment_score": 0.5,
                        "summary": f"Confirmed Revolut swing trade trigger (Target 1: {t[4]}, Stop: {t[6]})",
                        "updated_at": trade_time
                    })

        # 2. Ingest high-conviction BUY opportunities from market snapshots (within max_age)
        for symbol, data in intel.items():
            snap_time = data.get("updated_at", 0.0)
            if (
                symbol not in seen_symbols 
                and data["action"] == "BUY" 
                and data["composite_score"] >= min_score
                and (now - snap_time <= max_age)
            ):
                candidates.append(data)
                seen_symbols.add(symbol)

        # Sort descending by composite score
        candidates.sort(key=lambda x: x["composite_score"], reverse=True)
        return candidates[:max_candidates]

    def get_sentiment(self, binance_symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetches latest Revolut intelligence for a specific Binance symbol.
        Supports exact symbol lookup and base currency fallback matching.
        """
        intel = self.get_all_intel()
        if binance_symbol in intel:
            return intel[binance_symbol]
        
        base = binance_symbol.split('/')[0] if '/' in binance_symbol else binance_symbol
        for k, v in intel.items():
            if k.split('/')[0] == base:
                return v
        return None

    def is_emergency_sell(self, binance_symbol: str, sell_threshold: float = -0.60) -> Tuple[bool, str]:
        """
        Checks if Revolut bot has issued an emergency SELL signal or has an active short/sell trade on this asset.
        """
        # Check active trades for confirmed SELL
        active_trades = self.fetch_active_trades()
        for t in active_trades:
            ticker = t[1]
            action = t[2]
            if action == "SELL":
                base_sym = self.clean_base_symbol(ticker)
                target_base = binance_symbol.split('/')[0]
                if base_sym == target_base:
                    return True, f"Revolut Confirmed SELL Trade Active on {ticker} (Status: {t[7]})"

        # Check latest market snapshot score
        intel = self.get_sentiment(binance_symbol)
        if intel:
            composite_score = intel.get("composite_score", 0.0)
            action = intel.get("action", "HOLD")
            summary = intel.get("summary", "")

            if action == "SELL" and composite_score <= sell_threshold:
                return True, f"Revolut AI Emergency SELL (Score: {composite_score:+.2f}): {summary}"

        return False, ""

