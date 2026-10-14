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

    def evaluate(self, symbol: str, df: pd.DataFrame, min_atr_ratio: float = None) -> dict:
        result = {'signal': 'HOLD', 'source': None, 'priority_score': 0.0}
        if df.empty or len(df) < 27:
            return result
            
        # Core Indicators
        df['EMA_12'] = ta.ema(df['close'], length=12)
        df['EMA_26'] = ta.ema(df['close'], length=26)
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
                
        # Dynamic Price Spectrums
        atr = curr.get('ATR_14', 0)
        if atr > 0:
            buy_floor = min(curr[bbl], close - 0.75 * atr) if bbl else close - 0.75 * atr
            result['spectrum_buy'] = (buy_floor, close + 0.15 * atr)
            result['spectrum_sell_1'] = (close + 1.2 * atr, close + 1.8 * atr)
            result['spectrum_sell_2'] = (max(curr[bbu], close + 2.2 * atr) if bbu else close + 2.2 * atr, close + 2.8 * atr)
            result['invalidation'] = buy_floor - 1.0 * atr

        return result

