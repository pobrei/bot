import pandas as pd
import pandas_ta_classic as ta
import logging
from typing import Tuple, Dict, Any

from .config import (
    MIN_ATR_RATIO,
    ATR_SL_MULTIPLIER,
    ATR_TP_MR_MULTIPLIER,
    ATR_TP_MOM_MULTIPLIER,
    MAX_LOSS_CAP_PCT
)

logger = logging.getLogger(__name__)

def check_btc_macro_gate(btc_df: pd.DataFrame) -> Tuple[bool, str]:
    """
    Evaluates BTC 15m OHLCV to detect macro market crashes:
    1. If BTC drops > 1.2% in the current or previous 15m candle.
    2. OR if BTC 15m ADX > 30 and price is below 20-EMA (strong macro downtrend).
    Returns (macro_risk_off: bool, reason: str).
    """
    if btc_df is None or btc_df.empty or len(btc_df) < 22:
        return False, "Insufficient BTC data"

    curr_c = btc_df.iloc[-1]
    prev_c = btc_df.iloc[-2]
    
    # Check candle body drop
    curr_drop = (curr_c['close'] - curr_c['open']) / curr_c['open'] if curr_c['open'] > 0 else 0.0
    prev_drop = (prev_c['close'] - prev_c['open']) / prev_c['open'] if prev_c['open'] > 0 else 0.0

    # Also check candle-over-candle close drop
    if len(btc_df) >= 3:
        prev_prev_c = btc_df.iloc[-3]
        if prev_prev_c['close'] > 0:
            c2c_prev = (prev_c['close'] - prev_prev_c['close']) / prev_prev_c['close']
            prev_drop = min(prev_drop, c2c_prev)
        if prev_c['close'] > 0:
            c2c_curr = (curr_c['close'] - prev_c['close']) / prev_c['close']
            curr_drop = min(curr_drop, c2c_curr)

    worst_drop = min(curr_drop, prev_drop)
    if worst_drop <= -0.012: # -1.2%
        return True, f"BTC crash dump detected: {worst_drop*100:.2f}% <= -1.2%"

    # Strong Macro Downtrend Check (ADX > 30 and Close < 20-EMA)
    try:
        ema20 = ta.ema(btc_df['close'], length=20)
        adx_df = ta.adx(high=btc_df['high'], low=btc_df['low'], close=btc_df['close'], length=14)
        
        adx_val = None
        if adx_df is not None and 'ADX_14' in adx_df.columns:
            val = adx_df['ADX_14'].iloc[-2]
            if not pd.isna(val):
                adx_val = float(val)

        close_eval = float(prev_c['close'])
        ema_eval = float(ema20.iloc[-2]) if ema20 is not None and not pd.isna(ema20.iloc[-2]) else 0.0

        if adx_val is not None and adx_val > 30.0 and ema_eval > 0 and close_eval < ema_eval:
            return True, f"BTC macro downtrend: ADX {adx_val:.1f} > 30 and Close {close_eval:.1f} < 20-EMA {ema_eval:.1f}"
    except Exception as e:
        logger.debug(f"BTC Macro Gate indicator check error: {e}")

    return False, "BTC Macro Normal"


class Strategy:
    def __init__(self):
        self.last_regimes = {}

    def get_regime(self, symbol: str) -> str:
        return self.last_regimes.get(symbol, "UNKNOWN")

    def evaluate(self, symbol: str, df: pd.DataFrame, min_atr_ratio: float = None, macro_risk_off: bool = False) -> dict:
        result = {'signal': 'HOLD', 'source': None, 'priority_score': 0.0}
        if df.empty or len(df) < 28:
            return result
            
        # Core Indicators
        df['EMA_12'] = ta.ema(df['close'], length=12)
        df['EMA_26'] = ta.ema(df['close'], length=26)
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        # Volume SMA Shift: 20-bar Volume SMA on shifted bars so breakout volume doesn't inflate baseline
        df['Volume_SMA'] = df['volume'].shift(1).rolling(20).mean()
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

        # Donchian High: 20-period high strictly of prior bars
        df['Donchian_High_20'] = df['high'].shift(1).rolling(20).max()

        # ADX Regime Detection - populate indicators on df before candle slicing
        adx_val = None
        regime = "RANGING"  # Default
        
        if 'high' in df.columns and 'low' in df.columns:
            try:
                adx_df = ta.adx(high=df['high'], low=df['low'], close=df['close'], length=14)
                if adx_df is not None and not adx_df.empty:
                    for col in adx_df.columns:
                        df[col] = adx_df[col]
            except Exception as e:
                logger.debug(f"ADX calculation fallback: {e}")

        # Closed-bar evaluation:
        # df.iloc[-1] is the live forming candle (used for execution reference)
        # df.iloc[-2] is the last fully formed closed candle (eval_bar)
        # df.iloc[-3] is the prior closed candle (eval_prev)
        eval_prev = df.iloc[-3]
        eval_bar = df.iloc[-2]
        eval_close = float(eval_bar['close'])
        live_price = float(df.iloc[-1]['close']) if 'close' in df.columns else eval_close
        ref_price = live_price if live_price > 0 else eval_close

        if 'ADX_14' in df.columns and not pd.isna(eval_bar.get('ADX_14')):
            adx_val = float(eval_bar['ADX_14'])
            if adx_val >= 25.0:
                regime = "TRENDING"
                
        self.last_regimes[symbol] = regime

        # Volatility Filter evaluated on closed candle
        effective_hurdle = min_atr_ratio if min_atr_ratio is not None else MIN_ATR_RATIO
        atr_val = eval_bar.get('ATR_14')
        if pd.isna(atr_val) or (atr_val / eval_close) < effective_hurdle:
            return result
        atr = float(atr_val)

        # Mean Reversion (ADX < 25)
        if regime == "RANGING":
            if bbl and eval_close <= eval_bar.get(bbl, float('-inf')) and eval_bar.get('RSI_14', 50) <= 32:
                if macro_risk_off:
                    logger.warning(f"[{symbol}] Altcoin Mean Reversion BUY blocked by Macro BTC Crash Gate.")
                else:
                    result['signal'] = 'BUY'
                    result['source'] = 'mean_reversion'
                    result['priority_score'] = max(0.0, (32.0 - eval_bar.get('RSI_14', 50)) / 32.0)
                    
                    # Stop-Loss: max(entry - 1.5 * ATR, entry * 0.975) (capped at -2.5% max loss)
                    sl = max(ref_price - ATR_SL_MULTIPLIER * atr, ref_price * (1.0 - MAX_LOSS_CAP_PCT / 100.0))
                    # Take-Profit: entry + 2.0 * ATR (or BBM 20-SMA if net profit > +1.0%)
                    bbm_target = float(eval_bar[bbm]) if bbm and not pd.isna(eval_bar.get(bbm)) else None
                    if bbm_target and ((bbm_target - ref_price) / ref_price >= 0.010):
                        tp = bbm_target
                    else:
                        tp = ref_price + ATR_TP_MR_MULTIPLIER * atr
                        
                    result['stop_loss_price'] = sl
                    result['take_profit_price'] = tp
                    result['atr'] = atr
            elif (bbm and eval_close >= eval_bar.get(bbm, float('inf'))) or eval_bar.get('RSI_14', 50) >= 65:
                result['signal'] = 'SELL'
                result['source'] = 'mean_reversion'
                
        # Momentum Breakout & Trend Confluence (ADX >= 25)
        elif regime == "TRENDING":
            eval_vr = eval_bar['volume'] / eval_bar['Volume_SMA'] if eval_bar.get('Volume_SMA', 0) > 0 else 0
            prev_vr = eval_prev['volume'] / eval_prev['Volume_SMA'] if eval_prev.get('Volume_SMA', 0) > 0 else 0
            vol_ratio = max(eval_vr, prev_vr)
            candle_range = eval_bar['high'] - eval_bar['low']
            close_pos = (eval_close - eval_bar['low']) / candle_range if candle_range > 0 else 0
            
            # 1. Breakout setup
            rsi_curr = eval_bar.get('RSI_14', 50)
            ema12_dist = (eval_close - eval_bar.get('EMA_12', eval_close)) if 'EMA_12' in eval_bar else 0
            is_exhausted = (rsi_curr > 74.0) or (atr > 0 and ema12_dist > 2.8 * atr)

            if eval_close > eval_bar.get('Donchian_High_20', float('inf')) and vol_ratio >= 2.0 and close_pos >= 0.75 and not is_exhausted:
                result['signal'] = 'BUY'
                result['source'] = 'breakout'
                result['priority_score'] = min(1.0, (vol_ratio - 2.0) / 3.0)
                result['stop_loss_price'] = max(ref_price - ATR_SL_MULTIPLIER * atr, ref_price * (1.0 - MAX_LOSS_CAP_PCT / 100.0))
                result['take_profit_price'] = ref_price + ATR_TP_MOM_MULTIPLIER * atr
                result['trail_distance'] = ATR_SL_MULTIPLIER * atr
                result['atr'] = atr
            # 2. Trend Pullback setup (EMA trend alignment + upward momentum recovery)
            elif eval_bar.get('EMA_12', 0) > eval_bar.get('EMA_26', 0) and eval_close >= eval_bar.get('EMA_12', 0):
                rsi_prev = eval_prev.get('RSI_14', 50)
                is_green_and_higher = (eval_close >= eval_bar.get('open', eval_close)) and (eval_close > eval_prev['close'])
                if 45.0 <= rsi_curr <= 68.0 and rsi_curr > rsi_prev and is_green_and_higher:
                    result['signal'] = 'BUY'
                    result['source'] = 'breakout'
                    result['priority_score'] = (adx_val or 25.0) * 1.5
                    result['stop_loss_price'] = max(ref_price - ATR_SL_MULTIPLIER * atr, ref_price * (1.0 - MAX_LOSS_CAP_PCT / 100.0))
                    result['take_profit_price'] = ref_price + ATR_TP_MOM_MULTIPLIER * atr
                    result['trail_distance'] = ATR_SL_MULTIPLIER * atr
                    result['atr'] = atr
                
        # Dynamic Price Spectrums
        if atr > 0:
            buy_floor = min(eval_bar[bbl], ref_price - 0.75 * atr) if bbl else ref_price - 0.75 * atr
            result['spectrum_buy'] = (buy_floor, ref_price + 0.15 * atr)
            result['spectrum_sell_1'] = (ref_price + 1.2 * atr, ref_price + 1.8 * atr)
            result['spectrum_sell_2'] = (max(eval_bar[bbu], ref_price + 2.2 * atr) if bbu else ref_price + 2.2 * atr, ref_price + 2.8 * atr)
            result['invalidation'] = buy_floor - 1.0 * atr

        return result
