import unittest
from unittest.mock import MagicMock, patch
from src.risk import RiskEngine
from src.portfolio import PortfolioManager

class TestRiskExecution(unittest.TestCase):
    def setUp(self):
        self.mock_exchange = MagicMock()
        self.portfolio = PortfolioManager(self.mock_exchange)
        self.risk_engine = RiskEngine(self.portfolio)
        
        # Patch the functions in src.risk where they are used
        self.patcher_snapshots = patch('src.risk.get_equity_snapshots', return_value=[])
        self.patcher_kill = patch('src.risk.set_kill_switch')
        
        self.mock_snapshots = self.patcher_snapshots.start()
        self.mock_kill = self.patcher_kill.start()

    def tearDown(self):
        self.patcher_snapshots.stop()
        self.patcher_kill.stop()

    def test_sizing_tight_quote(self):
        # Scenario: Tight quote currency, checking formula logic
        # total_equity = 30, free_quote = 15, active_pos = 1
        # Formula: target is CAPITAL_PER_TRADE ($6.50), clamped to max 25% equity ($7.50) and slot quote ($12.0) -> $6.50
        self.portfolio.get_total_equity_usd = MagicMock(return_value=30.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=15.0)
        
        size = self.risk_engine.calculate_position_size(active_positions_count=1)
        self.assertAlmostEqual(size, 6.50, places=2)

    def test_sizing_capped_to_25_pct_equity(self):
        # Scenario: Low equity, 25% max equity cap triggers when CAPITAL_PER_TRADE exceeds 25% of equity
        # total_equity = 24.0 (25% is 6.00), free_quote = 20.0
        self.portfolio.get_total_equity_usd = MagicMock(return_value=24.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=20.0)
        
        size = self.risk_engine.calculate_position_size(active_positions_count=0)
        self.assertAlmostEqual(size, 6.00, places=2)

    def test_rejection_insufficient_quote(self):
        # Scenario: Available quote is below min_notional (6.00) after BNB reserve
        # total_equity = 30, free_quote = 6
        # Formula: (6 - 3) / 2 = 1.5 < 6.00
        self.portfolio.get_total_equity_usd = MagicMock(return_value=30.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=6.0)
        
        size = self.risk_engine.calculate_position_size(active_positions_count=0)
        self.assertAlmostEqual(size, 1.5, places=2)
        
        is_valid, reason, size_usd = self.risk_engine.validate_entry("BTC/USDC", 0, "USDC")
        self.assertFalse(is_valid)
        self.assertIn("insufficient to meet min notional", reason)

    def test_circuit_breaker_5_percent_baseline(self):
        # Scenario: Baseline was $30. Current is $28.40. Drop is 1.6 / 30 = 5.33% > 5%
        self.mock_snapshots.return_value = [{'equity': 30.0}]
        self.portfolio.get_total_equity_usd = MagicMock(return_value=28.40)
        
        is_safe, reason = self.risk_engine.check_circuit_breakers()
        self.assertFalse(is_safe)
        self.assertIn("Max daily drawdown breached", reason)
        self.mock_kill.assert_called_with(True)
        
    def test_circuit_breaker_emergency_floor(self):
        # Scenario: Absolute floor drops below $20
        self.portfolio.get_total_equity_usd = MagicMock(return_value=19.50)
        
        is_safe, reason = self.risk_engine.check_circuit_breakers()
        self.assertFalse(is_safe)
        self.assertIn("Equity floor breached", reason)
        self.mock_kill.assert_called_with(True)

    def test_bnb_fee_reserve_shield_in_execution(self):
        from src.execution import ExecutionManager
        from src import database
        
        mock_ex = MagicMock()
        mock_ex.amount_to_precision = lambda sym, amt: str(round(amt, 4))
        mock_ex.price_to_precision = lambda sym, pr: str(round(pr, 2))
        mock_ex.market.return_value = {
            'limits': {
                'amount': {'min': 0.001},
                'cost': {'min': 5.0}
            }
        }
        mock_ex.fetch_ticker.return_value = {'last': 700.0}

        port = PortfolioManager(mock_ex)
        # Total BNB is 0.020 (value ~$14.00)
        port.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.020}})
        
        executor = ExecutionManager(mock_ex, port)
        executor.dry_run = True

        # Case 1: Open position of 0.008 BNB exists. Sell must only sell 0.008, NOT 0.020!
        database.upsert_position({
            "symbol": "BNB/USDC",
            "entry_price": 700.0,
            "peak_price": 700.0,
            "amount": 0.008,
            "quote": "USDC",
            "trailing_active": False,
            "entry_time": "2026-10-15 00:00:00",
            "source": "breakout"
        })
        
        res = executor.execute_order("BNB/USDC", "sell")
        self.assertIsNotNone(res)
        self.assertEqual(res['amount'], 0.008)

        # Case 2: No position tracked, free BNB = 0.020.
        # Must preserve 0.005 reserve and only sell 0.015!
        database.remove_position("BNB/USDC")
        res = executor.execute_order("BNB/USDC", "sell")
        self.assertIsNotNone(res)
        self.assertEqual(res['amount'], 0.015)

        # Case 3: Free BNB is 0.004 (< 0.005 reserve). Must protect reserve and NOT sell!
        port.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.004}})
        res = executor.execute_order("BNB/USDC", "sell")
        self.assertEqual(res['status'], 'cleared_dust')
        self.assertEqual(res['amount'], 0.0)

if __name__ == '__main__':
    unittest.main()

