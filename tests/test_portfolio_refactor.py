import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.portfolio import PortfolioManager
from src.engine import TradingEngine
from src import database

class TestPortfolioRefactor(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_dry_run_state_has_no_duplicate_usdt(self):
        """Verify that default paper state has USDC and BNB only, never duplicate USDT."""
        portfolio = PortfolioManager()
        state = portfolio.dry_run_state
        self.assertIn('USDC', state)
        self.assertNotIn('USDT', state)
        self.assertEqual(state['USDC']['free'], 30.0)

    def test_portfolio_equity_does_not_double(self):
        """Verify that total equity calculation does not double count stables."""
        mock_ex = MagicMock()
        mock_ex.fetch_ticker.return_value = {'last': 600.0}
        portfolio = PortfolioManager(mock_ex)
        portfolio.dry_run_state = {
            'USDC': {'free': 24.72, 'used': 0.0, 'total': 24.72},
            'BNB': {'free': 0.0052, 'used': 0.0, 'total': 0.0052}
        }
        with patch('src.portfolio.DRY_RUN', True):
            equity = portfolio.get_total_equity_usd()
            # 24.72 + (0.0052 * 600) = 24.72 + 3.12 = 27.84
            self.assertAlmostEqual(equity, 27.84, places=2)

    def test_full_portfolio_state_database_roundtrip(self):
        """Verify saving and reading full portfolio state with live and paper balances."""
        paper_bals = {'USDC': {'free': 25.0, 'used': 0.0, 'total': 25.0}}
        live_bals = {'USDC': {'free': 24.72, 'used': 0.0, 'total': 24.72}, 'BNB': {'free': 0.005, 'used': 0.0, 'total': 0.005}}
        
        database.save_portfolio_state(
            total_equity=28.0,
            balances=paper_bals,
            live_equity=27.72,
            live_balances=live_bals
        )
        
        full = database.get_full_portfolio_state()
        self.assertEqual(full['total_equity'], 28.0)
        self.assertEqual(full['live_equity'], 27.72)
        self.assertIn('USDC', full['balances'])
        self.assertIn('BNB', full['live_balances'])

    def test_engine_initialization_and_sync(self):
        """Verify TradingEngine initializes cleanly and syncs portfolio state without crashing."""
        mock_ex = MagicMock()
        mock_ex.fetch_balance.return_value = {
            'total': {'USDC': 24.72, 'BNB': 0.0052},
            'free': {'USDC': 24.72, 'BNB': 0.0052},
            'used': {'USDC': 0.0, 'BNB': 0.0}
        }
        mock_ex.fetch_ticker.return_value = {'last': 600.0}
        
        with patch('src.engine.MarketData') as MockMD:
            md_instance = MagicMock()
            md_instance.exchange = mock_ex
            MockMD.return_value = md_instance
            
            engine = TradingEngine(skip_lock=True)
            engine.sync_portfolio_state()
            
            state = database.get_full_portfolio_state()
            self.assertIsNotNone(state['total_equity'])
            self.assertGreater(state['total_equity'], 0)

if __name__ == '__main__':
    unittest.main()
