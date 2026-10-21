import pytest
from datetime import datetime, timedelta
from src.position_tracker import PositionTracker
from src import database

def test_micro_scalp_stagnation_does_not_cut_green_trade_at_45m():
    """Verify that a developing trade with +0.25% gain is NOT cut at 45m."""
    tracker = PositionTracker()
    symbol = "TEST_GREEN/USDC"
    
    database.remove_position(symbol)
    database.clear_symbol_cooldown(symbol)

    # 50 minutes ago
    entry_time = (datetime.now() - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M:%S")
    pos_data = {
        "symbol": symbol,
        "entry_price": 100.0,
        "peak_price": 100.3,
        "amount": 0.1,
        "quote": "USDC",
        "trailing_active": False,
        "entry_time": entry_time,
        "source": "micro_momentum"
    }
    database.upsert_position(pos_data)

    # Price at 100.25 (+0.25% gain) - previously dumped at 45m, now held!
    exit_check = tracker.check_position_exit(symbol, current_price=100.25)
    assert exit_check is None

    database.remove_position(symbol)

def test_micro_scalp_stagnation_cuts_underwater_at_90m():
    """Verify that a dead trade (<= +0.15%) held for > 90m is safely exited."""
    tracker = PositionTracker()
    symbol = "TEST_DEAD/USDC"
    
    database.remove_position(symbol)
    database.clear_symbol_cooldown(symbol)

    # 95 minutes ago
    entry_time = (datetime.now() - timedelta(minutes=95)).strftime("%Y-%m-%d %H:%M:%S")
    pos_data = {
        "symbol": symbol,
        "entry_price": 100.0,
        "peak_price": 100.1,
        "amount": 0.1,
        "quote": "USDC",
        "trailing_active": False,
        "entry_time": entry_time,
        "source": "micro_momentum+revolut_ai"
    }
    database.upsert_position(pos_data)

    # Price at 100.05 (+0.05% gain)
    exit_check = tracker.check_position_exit(symbol, current_price=100.05)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "STAGNATION_TIMEOUT"
    assert "without momentum" in reason

    database.remove_position(symbol)

def test_trailing_floor_guarantees_fee_coverage():
    """Verify that breakeven floors are set at +0.35% minimum to guarantee net positive profit over fees."""
    tracker = PositionTracker()
    symbol = "TEST_FLOOR/USDC"
    
    database.remove_position(symbol)
    database.clear_symbol_cooldown(symbol)

    pos_data = {
        "symbol": symbol,
        "entry_price": 100.0,
        "peak_price": 101.5, # Reached +1.5%, arming trailing stop
        "amount": 0.1,
        "quote": "USDC",
        "trailing_active": True,
        "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "micro_momentum"
    }
    database.upsert_position(pos_data)

    # If price pulls back, trailing stop must not drop below +0.35% floor (100.35)
    # Price pulls back to 100.30 -> trigger exit
    exit_check = tracker.check_position_exit(symbol, current_price=100.30)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "TRAILING_STOP"

    database.remove_position(symbol)
