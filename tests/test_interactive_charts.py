import unittest
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src import database
from src.visualization import (
    create_interactive_candlestick, 
    create_equity_curve_chart, 
    get_tradingview_widget_html
)

class TestInteractiveCharts(unittest.TestCase):
    def setUp(self):
        # Create sample OHLCV DataFrame with indicators
        self.df = pd.DataFrame({
            'timestamp': [1700000000000 + i * 3600000 for i in range(30)],
            'open': [100.0 + i for i in range(30)],
            'high': [105.0 + i for i in range(30)],
            'low': [98.0 + i for i in range(30)],
            'close': [102.0 + i for i in range(30)],
            'volume': [1000 + i * 10 for i in range(30)],
            'EMA_12': [101.5 + i for i in range(30)],
            'EMA_26': [100.2 + i for i in range(30)],
            'RSI_14': [52.0 for i in range(30)],
            'BBU_5_2.0': [106.0 + i for i in range(30)],
            'BBL_5_2.0': [97.0 + i for i in range(30)],
        })

    def test_database_candles_cache_roundtrip(self):
        symbol = "TEST/USDC"
        timeframe = "1h"
        regime = "TRENDING"
        
        # Save to DB
        database.save_market_candles(symbol, timeframe, self.df, regime)
        
        # Read from DB
        cached = database.get_market_candles(symbol, timeframe)
        self.assertIsNotNone(cached)
        
        df_retrieved, regime_retrieved, updated_at = cached
        self.assertEqual(regime_retrieved, "TRENDING")
        self.assertTrue(len(df_retrieved) > 0)
        self.assertIn('close', df_retrieved.columns)
        self.assertIn('EMA_12', df_retrieved.columns)
        self.assertIn('RSI_14', df_retrieved.columns)
        self.assertAlmostEqual(float(df_retrieved['close'].iloc[-1]), float(self.df['close'].iloc[-1]), places=2)
        
        # Check symbol listing
        symbols = database.get_cached_market_symbols()
        self.assertIn("TEST/USDC", symbols)

    def test_create_interactive_candlestick(self):
        fig = create_interactive_candlestick("BTC/USDC", self.df, regime="TRENDING", timeframe="1h")
        self.assertIsInstance(fig, go.Figure)
        
        # Verify trace names exist (Candlestick, EMA, BB, Volume, RSI)
        trace_names = [t.name for t in fig.data if t.name]
        self.assertTrue(any("Price" in name for name in trace_names))
        self.assertTrue(any("EMA 12" in name for name in trace_names))
        self.assertTrue(any("EMA 26" in name for name in trace_names))
        self.assertTrue(any("Volume" in name for name in trace_names))
        self.assertTrue(any("RSI" in name for name in trace_names))
        
        # Verify dark layout
        self.assertEqual(fig.layout.plot_bgcolor, '#131722')
        self.assertEqual(fig.layout.paper_bgcolor, '#0e1117')

    def test_create_equity_curve_chart(self):
        snapshots = [
            {'timestamp': '2026-10-03T06:00:00', 'equity': 30.0},
            {'timestamp': '2026-10-03T07:00:00', 'equity': 30.5},
            {'timestamp': '2026-10-03T08:00:00', 'equity': 30.2},
        ]
        fig = create_equity_curve_chart(snapshots, current_equity=30.8, starting_capital=30.0)
        self.assertIsInstance(fig, go.Figure)
        self.assertTrue(len(fig.data) >= 1)
        self.assertEqual(fig.data[0].name, "Portfolio Equity")

    def test_tradingview_widget_html(self):
        html = get_tradingview_widget_html("ETH/USDC", timeframe="1h", height=500)
        self.assertIn("BINANCE:ETHUSDC", html)
        self.assertIn("s3.tradingview.com/tv.js", html)
        self.assertIn("TradingView.widget", html)

if __name__ == '__main__':
    unittest.main()
