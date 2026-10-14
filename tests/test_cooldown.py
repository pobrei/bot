import time
import pytest
from unittest.mock import MagicMock
from src import database
from src.risk import RiskEngine
from src.portfolio import PortfolioManager

def test_symbol_cooldown_lifecycle():
    symbol = "TEST/USDC"
    
    # 1. Clear any prior cooldown
    database.clear_symbol_cooldown(symbol)
    is_cool, reason = database.is_symbol_in_cooldown(symbol)
    assert is_cool is False

    # 2. Set cooldown for 2 hours
    database.set_symbol_cooldown(symbol, duration_seconds=7200, reason="Hard Stop-Loss hit")
    is_cool, reason = database.is_symbol_in_cooldown(symbol)
    assert is_cool is True
    assert "In cooldown" in reason
    assert "Hard Stop-Loss hit" in reason

    # 3. Test RiskEngine rejects entry on cooldown
    mock_exchange = MagicMock()
    portfolio = PortfolioManager(mock_exchange)
    risk_engine = RiskEngine(portfolio)
    
    is_valid, reject_reason, size = risk_engine.validate_entry(symbol, active_positions_count=0)
    assert is_valid is False
    assert "In cooldown" in reject_reason

    # 4. Clean up cooldown
    database.clear_symbol_cooldown(symbol)
    is_cool, _ = database.is_symbol_in_cooldown(symbol)
    assert is_cool is False

def test_differentiated_cooldown_win_vs_loss(tmp_path, monkeypatch):
    from src.position_tracker import PositionTracker
    tracker = PositionTracker()

    # 1. Test winning exit generates a short profit cooldown (45m)
    win_symbol = "WIN/USDC"
    database.clear_symbol_cooldown(win_symbol)
    tracker.record_entry(win_symbol, entry_price=10.0, amount=1.0, quote="USDC", source="breakout")
    tracker.record_exit(win_symbol, exit_price=10.5, reason="Take-Profit Hit") # +5% profit
    
    is_cool, reason = database.is_symbol_in_cooldown(win_symbol)
    assert is_cool is True
    assert "Profit Cooldown" in reason
    database.clear_symbol_cooldown(win_symbol)

    # 2. Test losing exit generates a long loss cooldown (4h)
    loss_symbol = "LOSS/USDC"
    database.clear_symbol_cooldown(loss_symbol)
    tracker.record_entry(loss_symbol, entry_price=10.0, amount=1.0, quote="USDC", source="breakout")
    tracker.record_exit(loss_symbol, exit_price=9.7, reason="Hard Stop-Loss hit") # -3% loss
    
    is_cool, reason = database.is_symbol_in_cooldown(loss_symbol)
    assert is_cool is True
    assert "Loss Cooldown" in reason
    database.clear_symbol_cooldown(loss_symbol)

def test_tiered_trailing_stop_breakeven_floor():
    from src.position_tracker import PositionTracker
    tracker = PositionTracker()
    symbol = "TRAIL/USDC"
    
    tracker.record_entry(symbol, entry_price=100.0, amount=1.0, quote="USDC", source="breakout")
    
    # Price rises to 101.6 (+1.6%), arming the trailing stop
    exit_signal = tracker.check_position_exit(symbol, current_price=101.6)
    assert exit_signal is None  # Still holding at peak
    
    pos = database.get_open_position(symbol)
    assert pos["trailing_active"] is True
    assert pos["peak_price"] == 101.6
    
    # Price pulls back to 100.3. Since breakeven floor is 100.4 (+0.4%), it MUST trigger exit!
    exit_signal = tracker.check_position_exit(symbol, current_price=100.3)
    assert exit_signal is not None
    exit_type, reason, pnl_pct = exit_signal
    assert exit_type == "TRAILING_STOP"
    assert pnl_pct > 0.0  # Still in net profit!
    
    database.remove_position(symbol)
    database.clear_symbol_cooldown(symbol)

def test_tiered_trailing_stop_runner_mode():
    from src.position_tracker import PositionTracker
    tracker = PositionTracker()
    symbol = "RUNNER/USDC"
    
    tracker.record_entry(symbol, entry_price=100.0, amount=1.0, quote="USDC", source="breakout")
    
    # Price surges to 104.0 (+4.0%), activating runner mode (trail tightens to 0.6%)
    tracker.check_position_exit(symbol, current_price=104.0)
    
    # 0.6% below peak 104.0 is: 104.0 * (1 - 0.006) = 103.376
    # Price drops to 103.35 -> should trigger trailing stop and lock in > 3.3% gain!
    exit_signal = tracker.check_position_exit(symbol, current_price=103.35)
    assert exit_signal is not None
    exit_type, reason, pnl_pct = exit_signal
    assert exit_type == "TRAILING_STOP"
    assert pnl_pct >= 3.3  # Locked in massive runner gain!
    
    database.remove_position(symbol)
    database.clear_symbol_cooldown(symbol)

def test_zero_equity_snapshot_shield_and_cache():
    # 1. Snapshot shielding: zero and negative values must be rejected
    prior_count = len(database.get_equity_snapshots(24))
    database.record_equity_snapshot(0.0)
    database.record_equity_snapshot(-15.0)
    database.record_equity_snapshot(None)
    new_count = len(database.get_equity_snapshots(24))
    assert new_count == prior_count  # No zero snapshots inserted!

    # 2. PortfolioManager fallback: when fetch_balances returns empty, preserve last known equity
    mock_exchange = MagicMock()
    portfolio = PortfolioManager(mock_exchange)
    portfolio._last_known_equity = 31.85
    
    # Mock exchange raising error / returning empty
    mock_exchange.fetch_balance.return_value = {}
    with monkeypatch_dry_run_false():
        equity = portfolio.get_total_equity_usd()
        assert equity == 31.85  # Protected! Does not return 0.0!

def test_breakout_anti_exhaustion_filter():
    import pandas as pd
    from unittest.mock import patch
    from src.strategy import Strategy
    
    strategy = Strategy()
    df = pd.DataFrame({
        'open': [100.0]*28 + [102.0, 102.0],
        'close': [100.0]*28 + [108.0, 108.0],
        'high': [101.0]*28 + [109.0, 109.0],
        'low': [99.0]*28 + [101.0, 101.0],
        'volume': [1000.0]*28 + [5000.0, 5000.0]  # Massive volume breakout on closed bar
    })
    
    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.sma') as mock_sma, \
         patch('src.strategy.ta.adx') as mock_adx:
        
        mock_atr.return_value = pd.Series([3.0]*30)
        mock_sma.return_value = pd.Series([1000.0]*30)
        mock_adx.return_value = pd.DataFrame({'ADX_14': [35.0]*30}) # TRENDING
        
        # Case A: Exhausted blow-off top (RSI = 85 like the SAND pump) -> MUST BE REJECTED!
        mock_rsi.return_value = pd.Series([60.0]*28 + [85.0, 85.0])
        res_exhausted = strategy.evaluate("EXHAUST/USDC", df)
        assert res_exhausted['signal'] == 'HOLD'
        
        # Case B: Fresh breakout (RSI = 65, healthy momentum) -> MUST TRIGGER BUY!
        mock_rsi.return_value = pd.Series([55.0]*28 + [65.0, 65.0])
        res_fresh = strategy.evaluate("FRESH/USDC", df)
        assert res_fresh['signal'] == 'BUY'
        assert res_fresh['source'] == 'breakout'

class monkeypatch_dry_run_false:
    def __enter__(self):
        from src import portfolio
        self.orig = portfolio.DRY_RUN
        portfolio.DRY_RUN = False
        return self
    def __exit__(self, *args):
        from src import portfolio
        portfolio.DRY_RUN = self.orig

def test_anti_chasing_guard_rejects_higher_price():
    from src.position_tracker import PositionTracker
    tracker = PositionTracker()
    symbol = "CHASE/USDC"
    
    mock_exchange = MagicMock()
    mock_exchange.fetch_balance.return_value = {'free': {'USDC': 50.0, 'BNB': 0.1}}
    portfolio = PortfolioManager(mock_exchange)
    portfolio.get_total_equity_usd = MagicMock(return_value=50.0)
    portfolio.get_available_quote_balance = MagicMock(return_value=50.0)
    portfolio.fetch_balances = MagicMock(return_value={'BNB': {'free': 0.1}})
    risk_engine = RiskEngine(portfolio)
    
    # 1. Record a position and an exit at $10.00
    tracker.record_entry(symbol, entry_price=9.5, amount=1.0, quote="USDC", source="breakout")
    tracker.record_exit(symbol, exit_price=10.0, reason="Take Profit Hit")
    
    # Clear the hard time cooldown to isolate the price check
    database.clear_symbol_cooldown(symbol)
    
    # 2. Try to buy at $10.50 (higher than exit price of $10.00) -> MUST REJECT!
    is_valid, reason, size = risk_engine.validate_entry(symbol, active_positions_count=0, quote="USDC", current_price=10.50)
    assert is_valid is False
    assert "Anti-chasing guard" in reason
    
    # 3. Try to buy at $9.80 (pulled back below exit price of $10.00) -> MUST ALLOW!
    is_valid, reason, size = risk_engine.validate_entry(symbol, active_positions_count=0, quote="USDC", current_price=9.80)
    assert is_valid is True
    assert size >= 6.0

def test_profit_cooldown_duration_is_90m():
    from src.position_tracker import PositionTracker
    tracker = PositionTracker()
    symbol = "COOLDOWN90/USDC"
    
    database.clear_symbol_cooldown(symbol)
    tracker.record_entry(symbol, entry_price=5.0, amount=2.0, quote="USDC", source="breakout")
    tracker.record_exit(symbol, exit_price=5.5, reason="Take Profit")
    
    cooldowns = database.get_active_cooldowns()
    target = next((c for c in cooldowns if c["symbol"] == symbol), None)
    assert target is not None
    # 90m cooldown should have between 88 and 90 mins remaining
    assert 88 <= target["mins_remaining"] <= 90
    assert "Profit Cooldown (90m)" in target["reason"]
    database.clear_symbol_cooldown(symbol)


