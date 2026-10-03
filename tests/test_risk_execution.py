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
        # Formula: min(max(30*0.33, 5.50), (15 - 3) / (2 - 1)) = min(9.9, 12) = 9.9
        self.portfolio.get_total_equity_usd = MagicMock(return_value=30.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=15.0)
        
        size = self.risk_engine.calculate_position_size(active_positions_count=1)
        self.assertAlmostEqual(size, 9.9, places=2)

    def test_sizing_max_equity(self):
        # Scenario: Lots of free quote, sizes clamp to 33% total equity
        # total_equity = 100, free_quote = 100, active_pos = 0
        # Formula: min(max(100*0.33, 5.50), (100 - 3) / 2) = min(33.0, 48.5) = 33.0
        self.portfolio.get_total_equity_usd = MagicMock(return_value=100.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=100.0)
        
        size = self.risk_engine.calculate_position_size(active_positions_count=0)
        self.assertAlmostEqual(size, 33.0, places=2)

    def test_rejection_insufficient_quote(self):
        # Scenario: Available quote is below min_notional (5.50) after BNB reserve
        # total_equity = 30, free_quote = 6
        # Formula: min(max(30*0.33, 5.50), (6 - 3) / 2) = min(9.9, 1.5) = 1.5
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

if __name__ == '__main__':
    unittest.main()
