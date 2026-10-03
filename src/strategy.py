import pandas as pd
import pandas_ta_classic as ta
import logging

from .sentiment import SentimentAnalyzer

logger = logging.getLogger(__name__)

class Strategy:
    def __init__(self):
        self.sentiment_analyzer = SentimentAnalyzer()
        self.last_regimes = {}

    def get_regime(self, symbol: str) -> str:
        return self.last_regimes.get(symbol, "UNKNOWN")

    def evaluate(self, symbol: str, df: pd.DataFrame) -> str:
        if df.empty or len(df) < 27:
            return 'HOLD'
            
        # Calculate Core Indicators
        df['EMA_12'] = ta.ema(df['close'], length=12)
        df['EMA_26'] = ta.ema(df['close'], length=26)
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        
        # MACD
        macd = ta.macd(df['close'])
        if macd is not None and not macd.empty:
            df = df.join(macd)
            macd_line = df.columns[-3]
            macd_signal = df.columns[-1]
        else:
            macd_line, macd_signal = None, None
            
        # Bollinger Bands
        bbands = ta.bbands(df['close'])
        if bbands is not None and not bbands.empty:
            df = df.join(bbands)
            bbl = df.columns[-5] # Lower Band
            bbu = df.columns[-3] # Upper Band
        else:
            bbl, bbu = None, None

        # ADX Regime Detection
        adx_val = None
        dmp_val = None
        dmn_val = None
        regime = "TRENDING"  # Default fallback

        if 'high' in df.columns and 'low' in df.columns:
            try:
                adx_df = ta.adx(high=df['high'], low=df['low'], close=df['close'], length=14)
                if adx_df is not None and not adx_df.empty:
                    if 'ADX_14' in adx_df.columns and not adx_df['ADX_14'].isna().iloc[-1]:
                        adx_val = float(adx_df['ADX_14'].iloc[-1])
                        dmp_val = float(adx_df['DMP_14'].iloc[-1])
                        dmn_val = float(adx_df['DMN_14'].iloc[-1])
                        
                        if adx_val >= 25.0:
                            regime = "TRENDING"
                        elif adx_val < 20.0:
                            regime = "RANGING"
                        else:
                            regime = "TRANSITIONAL"
            except Exception as e:
                logger.debug(f"ADX calculation fallback: {e}")

        self.last_regimes[symbol] = regime

        if df['EMA_12'].isna().iloc[-1] or df['EMA_26'].isna().iloc[-1] or df['RSI_14'].isna().iloc[-1]:
            return 'HOLD'

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Get sentiment
        sentiment = self.sentiment_analyzer.get_market_sentiment(symbol)
        
        adx_str = f", ADX: {adx_val:.1f} [{regime}]" if adx_val is not None else f" [{regime}]"
        logger.info(f"[{symbol}] EMA_12: {curr['EMA_12']:.2f}, EMA_26: {curr['EMA_26']:.2f}, RSI: {curr['RSI_14']:.2f}{adx_str}, Sentiment: {sentiment:.2f}")
        
        fast_cross_above = prev['EMA_12'] <= prev['EMA_26'] and curr['EMA_12'] > curr['EMA_26']
        fast_cross_below = prev['EMA_12'] >= prev['EMA_26'] and curr['EMA_12'] < curr['EMA_26']
        rsi = curr['RSI_14']

        is_bullish = False
        is_bearish = False

        if regime == "TRENDING":
            # TRENDING REGIME: Prioritize strong directional continuation
            # Fast EMA crosses above slow, directional index DMP > DMN, RSI momentum 45-72
            directional_ok = (dmp_val is None or dmn_val is None or dmp_val > dmn_val)
            is_bullish = fast_cross_above and directional_ok and (45 <= rsi <= 72) and sentiment > -0.15
            is_bearish = fast_cross_below or rsi > 75 or sentiment < -0.4

        elif regime == "RANGING":
            # RANGING / CHOPPY REGIME: Avoid crossover traps! Focus on Mean Reversion
            # Buy lower Bollinger Band dip with low RSI, exit at upper band
            bounce_lower_bb = (bbl and curr['close'] <= curr[bbl]) or (rsi < 35)
            is_bullish = bounce_lower_bb and sentiment > -0.25
            is_bearish = (bbu and curr['close'] >= curr[bbu]) or rsi > 65 or sentiment < -0.4

        else:
            # TRANSITIONAL REGIME: Standard multi-indicator confluence
            is_bullish = fast_cross_above and (50 <= rsi <= 70) and sentiment > -0.1
            if bbl and curr['close'] <= curr[bbl] and sentiment > 0.2 and rsi < 40:
                is_bullish = True
            is_bearish = rsi > 75 or fast_cross_below or sentiment < -0.5
        
        if is_bullish:
            return 'BUY'
        elif is_bearish:
            return 'SELL'
            
        return 'HOLD'
