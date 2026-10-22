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
        
        self.portfolio.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.0}})
        
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

    def test_conviction_tier1_single_slot_power(self):
        # Scenario: Total equity $30, free quote $28, 0 active positions, A+ setup
        self.portfolio.get_total_equity_usd = MagicMock(return_value=30.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=28.0)
        self.portfolio.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.010}})

        size = self.risk_engine.calculate_position_size(
            active_positions_count=0,
            priority_score=90.0,
            source="ai_pullback+revolut_ai"
        )
        # Sizing should be significantly larger than the standard $6.50 (e.g. >= $12.0)
        self.assertGreaterEqual(size, 12.0)
        self.assertLessEqual(size, 14.0)

    def test_conviction_tier1_with_active_slot(self):
        # Scenario: 1 position already active, A+ signal arrives, spendable quote $15
        self.portfolio.get_total_equity_usd = MagicMock(return_value=30.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=15.0)
        self.portfolio.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.010}})

        size = self.risk_engine.calculate_position_size(
            active_positions_count=1,
            priority_score=85.0,
            source="ai_pullback+revolut_ai"
        )
        self.assertGreaterEqual(size, 11.0)
        self.assertLessEqual(size, 12.5)

    def test_conviction_tier3_speculative(self):
        # Scenario: Speculative mean reversion bounce (low score), should clamp to min notional ($6.00)
        self.portfolio.get_total_equity_usd = MagicMock(return_value=30.0)
        self.portfolio.get_available_quote_balance = MagicMock(return_value=20.0)
        self.portfolio.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.010}})

        size = self.risk_engine.calculate_position_size(
            active_positions_count=0,
            priority_score=5.0,
            source="mean_reversion"
        )
        self.assertAlmostEqual(size, 6.0, places=1)

    def test_partial_take_profit_scale_out_eligible(self):
        from src.position_tracker import PositionTracker
        from src import database
        
        tracker = PositionTracker()
        # Mock open position with $12 notional (Tier 1 size)
        # Entry @ 10.0, amount 1.2 -> $12.0 cost
        tracker.record_entry("TEST/USDC", entry_price=10.0, amount=1.2, source="ai_pullback+revolut_ai")
        
        # Current price rises to 10.25 (+2.5% gain >= 2.2% partial TP threshold)
        # 50% notional = 1.2 * 0.5 * 10.25 = $6.15 >= $5.20 MIN_PARTIAL_NOTIONAL
        exit_check = tracker.check_position_exit("TEST/USDC", current_price=10.25)
        self.assertIsNotNone(exit_check)
        exit_type, reason, pnl_pct = exit_check
        self.assertEqual(exit_type, "PARTIAL_TAKE_PROFIT")
        self.assertIn("50% scale-out", reason)
        self.assertAlmostEqual(pnl_pct, 2.5, places=1)

        # Record partial exit of 0.6 units
        partial_rec = tracker.record_partial_exit("TEST/USDC", exit_price=10.25, sold_amount=0.6, reason=reason)
        self.assertEqual(partial_rec["amount"], 0.6)
        self.assertAlmostEqual(partial_rec["realized_pnl"], 0.15, places=2)

        # Check remaining position in database
        pos = database.get_open_position("TEST/USDC")
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["amount"], 0.6, places=4)
        self.assertTrue(pos["partial_tp_taken"])
        self.assertTrue(pos["breakeven_locked"])
        self.assertTrue(pos["trailing_active"])

        # Clean up test position
        database.remove_position("TEST/USDC")

    def test_partial_take_profit_clamp_below_min_notional(self):
        from src.position_tracker import PositionTracker
        from src import database
        
        tracker = PositionTracker()
        # Small position: $6.00 notional (Tier 2/3 size)
        tracker.record_entry("SMALL/USDC", entry_price=10.0, amount=0.6, source="breakout")
        
        # Price rises to 10.25 (+2.5% gain)
        # 50% notional = 0.6 * 0.5 * 10.25 = $3.075 < $5.20 MIN_PARTIAL_NOTIONAL
        # Must NOT trigger partial scale-out to prevent Binance MIN_NOTIONAL error!
        exit_check = tracker.check_position_exit("SMALL/USDC", current_price=10.25)
        # Should NOT be PARTIAL_TAKE_PROFIT (might be None since +2.5% hasn't hit full TP of +5% or trailing stop)
        if exit_check is not None:
            self.assertNotEqual(exit_check[0], "PARTIAL_TAKE_PROFIT")

        # Clean up
        database.remove_position("SMALL/USDC")

    def test_runner_breakeven_lock_after_partial(self):
        from src.position_tracker import PositionTracker
        from src import database
        
        tracker = PositionTracker()
        tracker.record_entry("RUNNER/USDC", entry_price=10.0, amount=1.2, source="ai_pullback")
        # Trigger partial exit
        tracker.record_partial_exit("RUNNER/USDC", exit_price=10.25, sold_amount=0.6, reason="Partial TP")
        
        # Runner reaches peak of 10.35 (+3.5%), then pulls back
        tracker.check_position_exit("RUNNER/USDC", current_price=10.35)
        
        # Pulls back to 10.05 (+0.5%, above entry + 0.3% floor)
        # Since peak was 10.35 and runner floor at peak 3.5% is +1.8% (10.18):
        # 10.05 is below the runner floor of 10.18, so it MUST exit via TRAILING_STOP in profit!
        exit_check = tracker.check_position_exit("RUNNER/USDC", current_price=10.05)
        self.assertIsNotNone(exit_check)
        self.assertEqual(exit_check[0], "TRAILING_STOP")
        self.assertGreater(exit_check[2], 0.0)  # PnL percentage is strictly positive!

        # Clean up
        database.remove_position("RUNNER/USDC")

    def test_atr_adaptive_volatility_exit(self):
        from src.position_tracker import PositionTracker
        from src import database
        
        tracker = PositionTracker()
        # Asset with high ATR of 2.5% (0.025)
        tracker.record_entry("VOL/USDC", entry_price=100.0, amount=0.06, source="breakout", atr_pct=0.025)
        
        # In high volatility, trailing stop distance adapts: 1.5 * 2.5% = 3.75% -> clamped to 3.5%
        # Target TP adapts: 2.5% * 350.0 = 8.75% (instead of default 5.0%)
        # Price hits 105.0 (+5.0%). With default TP it would exit at +5.0%, but with high-ATR adaptive exit, it lets the runner ride!
        exit_check = tracker.check_position_exit("VOL/USDC", current_price=105.0)
        # At +5.0%, adaptive TP is 8.75%, so it should HOLD (None)
        self.assertIsNone(exit_check)

        # When price reaches 109.0 (+9.0% >= 8.75%), it triggers adaptive TAKE_PROFIT
        exit_check_tp = tracker.check_position_exit("VOL/USDC", current_price=109.0)
        self.assertIsNotNone(exit_check_tp)
        self.assertEqual(exit_check_tp[0], "TAKE_PROFIT")

        # Clean up
        database.remove_position("VOL/USDC")

    def test_execution_manager_explicit_amount_sell(self):
        from src.execution import ExecutionManager
        mock_ex = MagicMock()
        mock_ex.market = MagicMock(return_value={'limits': {'cost': {'min': 5.0}, 'amount': {'min': 0.001}}})
        mock_ex.fetch_ticker = MagicMock(return_value={'last': 10.0})
        mock_ex.price_to_precision = MagicMock(side_effect=lambda s, p: p)
        mock_ex.amount_to_precision = MagicMock(side_effect=lambda s, a: a)
        
        port = MagicMock()
        port.fetch_balances = MagicMock(return_value={'TEST': {'free': 1.0}})
        
        executor = ExecutionManager(mock_ex, port)
        executor.dry_run = True
        
        # Test selling explicit amount of 0.5 (50% scale out)
        res = executor.execute_order("TEST/USDC", "sell", amount=0.5)
        self.assertIsNotNone(res)
        self.assertEqual(res['amount'], 0.5)
        self.assertEqual(res['price'], 10.0)

if __name__ == '__main__':
    unittest.main()


