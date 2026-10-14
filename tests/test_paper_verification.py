import unittest
import pandas as pd
import numpy as np

from src.visualization import calculate_dynamic_price_spectrums
from src.paper_verification import EventDrivenPaperEngine, calculate_psr

class TestPaperVerificationAndSpectrums(unittest.TestCase):
    def setUp(self):
        # 100 periods of simulated OHLCV data
        np.random.seed(42)
        n = 100
        closes = 100.0 + np.cumsum(np.random.normal(0, 1.5, n))
        highs = closes + np.random.uniform(0.5, 2.0, n)
        lows = closes - np.random.uniform(0.5, 2.0, n)
        opens = (highs + lows) / 2.0
        volumes = np.random.uniform(1000, 5000, n)

        self.df = pd.DataFrame({
            'timestamp': [1700000000000 + i * 900000 for i in range(n)],
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
            'ATR_14': [1.8] * n,
            'BBL_20_2.0': [95.0] * n,
            'BBU_20_2.0': [108.0] * n,
            'BBM_20_2.0': [101.5] * n,
            'RSI_14': [30.0] * n
        })

    def test_calculate_dynamic_price_spectrums(self):
        spectrums = calculate_dynamic_price_spectrums(self.df)
        self.assertIsNotNone(spectrums)
        self.assertIn('buy_floor', spectrums)
        self.assertIn('buy_ceiling', spectrums)
        self.assertIn('tp1_floor', spectrums)
        self.assertIn('tp1_ceiling', spectrums)
        self.assertIn('tp2_floor', spectrums)
        self.assertIn('tp2_ceiling', spectrums)
        self.assertIn('invalidation', spectrums)
        self.assertIn('status', spectrums)

        # Invalidation must be below buy_floor
        self.assertLess(spectrums['invalidation'], spectrums['buy_floor'])
        # TP1 must be higher than close
        self.assertGreater(spectrums['tp1_floor'], spectrums['close'])
        # TP2 must be higher than TP1
        self.assertGreater(spectrums['tp2_floor'], spectrums['tp1_ceiling'])

    def test_calculate_psr(self):
        # Series of positive returns should yield high PSR
        pos_returns = pd.Series([0.01, 0.02, 0.005, 0.015, -0.002, 0.01, 0.025] * 10)
        psr = calculate_psr(pos_returns, benchmark_sr=0.0)
        self.assertGreater(psr, 0.90)

        # Series of negative returns should yield low PSR
        neg_returns = pd.Series([-0.01, -0.02, -0.005, -0.015, 0.002, -0.01] * 10)
        psr_neg = calculate_psr(neg_returns, benchmark_sr=0.0)
        self.assertLess(psr_neg, 0.10)

    def test_event_driven_paper_engine(self):
        engine = EventDrivenPaperEngine(starting_capital=30.0, timeframe="15m")
        results = engine.run_simulation(self.df, symbol="TEST/USDC", walk_forward_split=0.70)
        
        self.assertIn('summary', results)
        self.assertIn('in_sample', results)
        self.assertIn('out_of_sample', results)
        self.assertIn('trade_log', results)
        self.assertIn('equity_curve', results)

        summary = results['summary']
        self.assertIn('total_trades', summary)
        self.assertIn('win_rate_pct', summary)
        self.assertIn('profit_factor', summary)
        self.assertIn('psr', summary)
        self.assertTrue(len(results['equity_curve']) > 0)

if __name__ == '__main__':
    unittest.main()
