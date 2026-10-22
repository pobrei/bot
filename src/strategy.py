from typing import Tuple, Dict, Any, Optional
import pandas as pd
import pandas_ta_classic as ta
import logging

from .config import MIN_ATR_RATIO

logger = logging.getLogger(__name__)

class Strategy:
    def __init__(self):
        self.last_regimes = {}

    def get_regime(self, symbol: str) -> str:
        return self.last_regimes.get(symbol, "UNKNOWN")

    @staticmethod
    def check_btc_macro_gate(btc_df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Evaluates Bitcoin macro market condition to protect altcoin positions during market-wide dumps.
        Returns: (is_safe: bool, reason: str)
        """
        if btc_df is None or btc_df.empty or len(btc_df) < 50:
            return True, "Insufficient BTC bars, allowing trades"

        close_series = btc_df['close']
        ema_20 = ta.ema(close_series, length=20)
        ema_50 = ta.ema(close_series, length=50)
        curr = btc_df.iloc[-1]
        prev = btc_df.iloc[-2]
        prev2 = btc_df.iloc[-3] if len(btc_df) >= 3 else prev
        prev4 = btc_df.iloc[-5] if len(btc_df) >= 5 else prev2

        curr_close = float(curr['close'])
        curr_ema20 = float(ema_20.iloc[-1]) if ema_20 is not None and not pd.isna(ema_20.iloc[-1]) else curr_close
        curr_ema50 = float(ema_50.iloc[-1]) if ema_50 is not None and not pd.isna(ema_50.iloc[-1]) else curr_close

        # 1. Fast Dump Gate: 2-bar drop > 0.6% or 4-bar drop > 1.0% (1h)
        two_bar_ret = ((curr_close - float(prev2['close'])) / float(prev2['close'])) * 100.0
        four_bar_ret = ((curr_close - float(prev4['close'])) / float(prev4['close'])) * 100.0
        if two_bar_ret < -0.60:
            return False, f"BTC sharp dump: {two_bar_ret:.2f}% over last 2 bars"
        if four_bar_ret < -1.00:
            return False, f"BTC multi-bar dump: {four_bar_ret:.2f}% over last 4 bars (1h)"

        # 2. Structural Downtrend Gate: Price trading below 50 EMA and below 20 EMA
        # Strictly blocks altcoin buys during macro downtrends (eliminates single-candle green dead-cat bounce bypass)
        if curr_close < curr_ema50 * 0.998 and curr_close < curr_ema20:
            return False, f"BTC below 50 EMA ({curr_close:.1f} < {curr_ema50:.1f}) in active downward leg"

        return True, "BTC Macro Gate OK"

    @staticmethod
    def check_micro_momentum(
        symbol: str, 
        micro_df: pd.DataFrame, 
        rev_score: float = 0.0,
        min_vol_ratio: float = 2.2
    ) -> dict:
        """
        Dual-Timeframe Micro-Momentum Scalper (3m/5m).
        Detects early volume surges, moving-average stacks, and bullish pressure during favorable AI context.
        Returns: {'signal': 'BUY' | 'HOLD', 'source': 'micro_momentum', 'priority_score': float}
        """
        result = {'signal': 'HOLD', 'source': None, 'priority_score': 0.0}
        if micro_df is None or micro_df.empty or len(micro_df) < 25:
            return result

        # 1. AI Gate: Only scalp assets with neutral-to-positive AI conviction
        if rev_score < 0.20:
            return result

        # 2. Indicators on micro timeframe (3m / 5m)
        close = micro_df['close']
        volume = micro_df['volume']
        ema_9 = ta.ema(close, length=9)
        ema_21 = ta.ema(close, length=21)
        vol_sma = ta.sma(volume, length=20)
        rsi = ta.rsi(close, length=14)

        if ema_9 is None or ema_21 is None or vol_sma is None or rsi is None:
            return result

        curr = micro_df.iloc[-1]
        prev = micro_df.iloc[-2]

        curr_close = float(curr['close'])
        curr_open = float(curr.get('open', curr_close))
        curr_high = float(curr.get('high', curr_close))
        curr_low = float(curr.get('low', curr_close))

        curr_ema9 = float(ema_9.iloc[-1])
        curr_ema21 = float(ema_21.iloc[-1])
        curr_vol_sma = float(vol_sma.iloc[-1]) if vol_sma.iloc[-1] > 0 else 1.0
        curr_rsi = float(rsi.iloc[-1])

        curr_vol_ratio = float(curr['volume']) / curr_vol_sma
        prev_vol_ratio = float(prev['volume']) / float(vol_sma.iloc[-2]) if vol_sma.iloc[-2] > 0 else 1.0
        max_vol_ratio = max(curr_vol_ratio, prev_vol_ratio)

        # 3. Setup Rules:
        # A. Volume Surge: >= min_vol_ratio (e.g. 2.2x volume SMA)
        has_volume_surge = max_vol_ratio >= min_vol_ratio

        # B. Trend Alignment: EMA 9 > EMA 21 and price closing above EMA 9
        is_trend_aligned = (curr_ema9 > curr_ema21) and (curr_close >= curr_ema9 * 0.999)

        # C. Momentum Expansion: RSI between 50 and 78 (strong momentum, not exhausted)
        is_rsi_healthy = 50.0 <= curr_rsi <= 78.0

        # D. Bullish Candle Pressure: Close in upper 40% of candle range and green
        candle_range = curr_high - curr_low
        close_pos = (curr_close - curr_low) / candle_range if candle_range > 0 else 0.5
        is_bullish_candle = (curr_close >= curr_open) and (close_pos >= 0.60)

        atr_series = ta.atr(micro_df['high'], micro_df['low'], micro_df['close'], length=14)
        curr_atr = float(atr_series.iloc[-1]) if atr_series is not None and not pd.isna(atr_series.iloc[-1]) else 0.0
        result['atr_pct'] = (curr_atr / curr_close) if curr_close > 0 else 0.0

        if has_volume_surge and is_trend_aligned and is_rsi_healthy and is_bullish_candle:
            result['signal'] = 'BUY'
            result['source'] = 'micro_momentum'
            result['priority_score'] = 45.0 + (max_vol_ratio * 10.0) + (rev_score * 20.0)

        return result

    def evaluate(self, symbol: str, df: pd.DataFrame, min_atr_ratio: float = None, rev_score: float = 0.0) -> dict:
        result = {'signal': 'HOLD', 'source': None, 'priority_score': 0.0}
        if df.empty or len(df) < 27:
            return result
            
        # Core Indicators
        df['EMA_12'] = ta.ema(df['close'], length=12)
        df['EMA_26'] = ta.ema(df['close'], length=26)
        if len(df) >= 50:
            df['EMA_50'] = ta.ema(df['close'], length=50)
        else:
            df['EMA_50'] = df['EMA_26']
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        df['Volume_SMA'] = ta.sma(df['volume'], length=20)
        df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        # Bollinger Bands - in-place assignment to preserve columns for caller and DB
        bbands = ta.bbands(df['close'], length=20, std=2.0)
        bbl, bbm, bbu = None, None, None
        if bbands is not None and not bbands.empty:
            for col in bbands.columns:
                df[col] = bbands[col]
            bbl = next((c for c in bbands.columns if c.startswith('BBL')), None)
            bbm = next((c for c in bbands.columns if c.startswith('BBM')), None)
            bbu = next((c for c in bbands.columns if c.startswith('BBU')), None)

        # Donchian High
        df['Donchian_High_20'] = df['high'].rolling(20).max().shift(1)

        # ADX Regime Detection - in-place assignment
        adx_val = None
        regime = "RANGING"  # Default
        
        if 'high' in df.columns and 'low' in df.columns:
            try:
                adx_df = ta.adx(high=df['high'], low=df['low'], close=df['close'], length=14)
                if adx_df is not None and not adx_df.empty:
                    for col in adx_df.columns:
                        df[col] = adx_df[col]
                    if 'ADX_14' in df.columns and not pd.isna(df['ADX_14'].iloc[-1]):
                        adx_val = float(df['ADX_14'].iloc[-1])
                        if adx_val >= 25.0:
                            regime = "TRENDING"
            except Exception as e:
                logger.debug(f"ADX calculation fallback: {e}")
                
        self.last_regimes[symbol] = regime

        prev = df.iloc[-2]
        curr = df.iloc[-1]
        close = curr['close']
        
        # Volatility Filter
        effective_hurdle = min_atr_ratio if min_atr_ratio is not None else MIN_ATR_RATIO
        if pd.isna(curr.get('ATR_14')) or (curr['ATR_14'] / close) < effective_hurdle:
            return result

        # Mean Reversion (ADX < 25)
        if regime == "RANGING":
            if bbl and close <= curr[bbl] and curr.get('RSI_14', 50) <= 32:
                result['signal'] = 'BUY'
                result['source'] = 'mean_reversion'
                result['priority_score'] = max(0.0, (32.0 - curr.get('RSI_14', 50)) / 32.0)
            elif (bbm and close >= curr[bbm]) or curr.get('RSI_14', 50) >= 65:
                result['signal'] = 'SELL'
                result['source'] = 'mean_reversion'
                
        # Momentum Breakout & Trend Confluence (ADX >= 25)
        elif regime == "TRENDING":
            curr_vr = curr['volume'] / curr['Volume_SMA'] if curr.get('Volume_SMA', 0) > 0 else 0
            prev_vr = prev['volume'] / prev['Volume_SMA'] if prev.get('Volume_SMA', 0) > 0 else 0
            vol_ratio = max(curr_vr, prev_vr)
            candle_range = curr['high'] - curr['low']
            close_pos = (close - curr['low']) / candle_range if candle_range > 0 else 0
            
            # 1. Breakout setup
            # Anti-exhaustion filter: reject blow-off climaxes (RSI > 74 or extended > 2.8 ATR from EMA12)
            rsi_curr = curr.get('RSI_14', 50)
            atr_val = curr.get('ATR_14', 0)
            ema12_dist = (close - curr.get('EMA_12', close)) if 'EMA_12' in curr else 0
            is_exhausted = (rsi_curr > 74.0) or (atr_val > 0 and ema12_dist > 2.8 * atr_val)

            if close > curr.get('Donchian_High_20', float('inf')) and vol_ratio >= 2.0 and close_pos >= 0.75 and not is_exhausted:
                result['signal'] = 'BUY'
                result['source'] = 'breakout'
                result['priority_score'] = min(1.0, (vol_ratio - 2.0) / 3.0)
            # 2. Trend Pullback setup (EMA trend alignment + upward momentum recovery)
            elif curr.get('EMA_12', 0) > curr.get('EMA_26', 0) and close >= curr.get('EMA_12', 0):
                rsi_curr = curr.get('RSI_14', 50)
                rsi_prev = prev.get('RSI_14', 50)
                is_green_and_higher = (close >= curr.get('open', close)) and (close > prev['close'])
                if 45.0 <= rsi_curr <= 68.0 and rsi_curr > rsi_prev and is_green_and_higher:
                    result['signal'] = 'BUY'
                    result['source'] = 'breakout'
                    result['priority_score'] = (adx_val or 25.0) * 1.5

        # 3. AI-Confirmed Pullback setup (Buy high-conviction dips in established uptrends)
        if result['signal'] == 'HOLD' and rev_score >= 0.35 and 'EMA_50' in df.columns:
            ema50_val = curr.get('EMA_50')
            if not pd.isna(ema50_val) and ema50_val > 0:
                ema26_val = curr.get('EMA_26', ema50_val)
                rsi_val = curr.get('RSI_14', 50)
                
                # Uptrend structure: price holds above or within 1.0% of 50 EMA
                in_uptrend_structure = close >= (ema50_val * 0.99)
                
                # Pullback criteria: price has dipped near EMA26 / lower BB / RSI 34-48
                is_dip = (close <= ema26_val * 1.008) or (bbl and close <= curr[bbl] * 1.015) or (34.0 <= rsi_val <= 48.0)
                
                # Stabilization confirmation: green candle or hammer lower wick
                candle_range = curr['high'] - curr['low']
                lower_wick = min(curr.get('open', close), close) - curr['low']
                has_support_bounce = (close >= curr.get('open', close)) or (candle_range > 0 and (lower_wick / candle_range) >= 0.35)
                
                if in_uptrend_structure and is_dip and has_support_bounce and 32.0 <= rsi_val <= 55.0:
                    result['signal'] = 'BUY'
                    result['source'] = 'ai_pullback'
                    result['priority_score'] = 50.0 + (rev_score * 25.0) + (55.0 - rsi_val)
                
        # Dynamic Price Spectrums
        atr = curr.get('ATR_14', 0)
        if atr > 0:
            result['atr_pct'] = (atr / close) if close > 0 else 0.0
            buy_floor = min(curr[bbl], close - 0.75 * atr) if bbl else close - 0.75 * atr
            result['spectrum_buy'] = (buy_floor, close + 0.15 * atr)
            result['spectrum_sell_1'] = (close + 1.2 * atr, close + 1.8 * atr)
            result['spectrum_sell_2'] = (max(curr[bbu], close + 2.2 * atr) if bbu else close + 2.2 * atr, close + 2.8 * atr)
            result['invalidation'] = buy_floor - 1.0 * atr
        else:
            result['atr_pct'] = 0.0

        return result

