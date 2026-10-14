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

if __name__ == '__main__':
    unittest.main()
