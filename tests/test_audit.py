import pytest
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch
from src.strategy import Strategy, check_btc_macro_gate
from src.position_tracker import PositionTracker
from src.execution import ExecutionManager
from src.portfolio import PortfolioManager
from src.risk import RiskEngine
from src import database
from src.config import MAX_SPREAD_PCT

def test_shifted_donchian_breakout_on_closed_bar():
    """
    Test 1: Shifted Donchian & Volume SMA on closed bar (iloc[-2]).
    Verifies that the 20-period high and volume SMA are computed on shifted bars,
    allowing a closed breakout candle to trigger BUY with dynamic ATR targets.
    """
    strategy = Strategy()
    
    # 20 bars of flat high=102, low=98, close=100, volume=1000
    # Followed by Bar 28 (iloc[-2], closed candle): high=110, close=109, volume=3500 (breakout!)
    # Followed by Bar 29 (iloc[-1], live forming candle): close=109.5
    closes = [100.0] * 28 + [109.0, 109.5]
    highs = [102.0] * 28 + [110.0, 110.0]
    lows = [98.0] * 28 + [100.0, 100.0]
    volumes = [1000.0] * 28 + [3500.0, 1200.0]
    
    df = pd.DataFrame({
        'open': [100.0] * 28 + [101.0, 109.0],
        'close': closes,
        'high': highs,
        'low': lows,
        'volume': volumes
    })

    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.adx') as mock_adx:
         
        mock_rsi.return_value = pd.Series([55.0] * 30)
        mock_atr.return_value = pd.Series([3.0] * 30)
        mock_adx.return_value = pd.DataFrame({'ADX_14': [32.0] * 30}) # Trending

        res = strategy.evaluate("SOL/USDC", df)
        
        # Verify signal triggers on closed bar (iloc[-2])
        assert res['signal'] == 'BUY'
        assert res['source'] == 'breakout'
        assert res['priority_score'] > 0.0
        
        # Verify dynamic ATR targets
        entry_ref = 109.5  # Live price iloc[-1]
        assert res['atr'] == 3.0
        # SL = max(entry - 1.5 * 3.0, entry * 0.975) = max(105.0, 106.7625) = 106.7625
        expected_sl = max(entry_ref - 1.5 * 3.0, entry_ref * 0.975)
        assert abs(res['stop_loss_price'] - expected_sl) < 1e-4
        # TP = entry + 3.5 * 3.0 = 109.5 + 10.5 = 120.0
        assert abs(res['take_profit_price'] - (entry_ref + 3.5 * 3.0)) < 1e-4

def test_breakeven_ratchet_fee_lock():
    """
    Test 2: Breakeven Ratchet (+1.2% unrealized gain -> stop raised to entry * 1.003).
    Ensures that as soon as gain hits +1.2%, SQLite stop_loss_price is ratcheted
    up to +0.3% to lock in fees, guaranteeing a risk-free trade.
    """
    tracker = PositionTracker()
    symbol = "RATCHET/USDC"
    entry_price = 100.0
    
    # 1. Open position with standard ATR stop
    tracker.record_entry(symbol, entry_price=entry_price, amount=1.0, quote="USDC", source="breakout", atr=2.0)
    pos = database.get_open_position(symbol)
    initial_sl = pos["stop_loss_price"]
    assert initial_sl < 100.0  # Initial stop below entry
    assert pos["ratcheted"] is False

    # 2. Price rises to +0.8% (100.8) -> not yet at +1.2% trigger
    exit_check = tracker.check_position_exit(symbol, current_price=100.80)
    assert exit_check is None
    pos = database.get_open_position(symbol)
    assert pos["ratcheted"] is False
    assert pos["stop_loss_price"] == initial_sl

    # 3. Price surges to +1.25% (101.25) -> triggers Breakeven Ratchet!
    exit_check = tracker.check_position_exit(symbol, current_price=101.25)
    assert exit_check is None  # Still holding
    pos = database.get_open_position(symbol)
    assert pos["ratcheted"] is True
    expected_ratchet_stop = entry_price * 1.003  # 100.30 (+0.3% fee lock)
    assert abs(pos["stop_loss_price"] - expected_ratchet_stop) < 1e-4

    # 4. Price pulls back to 100.25 (below 100.30 ratchet stop) -> exits with fee-locked profit!
    exit_check = tracker.check_position_exit(symbol, current_price=100.25)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "BREAKEVEN_STOP"
    assert "Breakeven Ratchet Stop hit" in reason
    assert pnl_pct > 0.0  # Closed in profit covering fees

    database.remove_position(symbol)
    database.clear_symbol_cooldown(symbol)

def test_order_book_spread_gate():
    """
    Test 3: Order Book Spread Gate (reject BUY when (ask - bid) / bid > 0.0018).
    Protects micro-capital from illiquid pair slippage.
    """
    mock_exchange = Mock()
    mock_exchange.market.return_value = {
        'limits': {'cost': {'min': 5.0}, 'amount': {'min': 0.001}}
    }
    mock_exchange.amount_to_precision.side_effect = lambda sym, amt: f"{amt:.4f}"
    mock_exchange.price_to_precision.side_effect = lambda sym, p: f"{p:.4f}"

    executor = ExecutionManager(mock_exchange)
    executor.dry_run = True

    # Case A: Wide spread (Bid: 100.0, Ask: 100.25 -> spread = 0.25% > 0.18%) -> REJECTED!
    mock_exchange.fetch_ticker.return_value = {
        'last': 100.10,
        'bid': 100.0,
        'ask': 100.25
    }
    res_rejected = executor.execute_order("WIDE/USDC", "buy", amount_usd=10.0)
    assert res_rejected is None

    # Case B: Tight spread (Bid: 100.0, Ask: 100.10 -> spread = 0.10% <= 0.18%) -> APPROVED!
    mock_exchange.fetch_ticker.return_value = {
        'last': 100.05,
        'bid': 100.0,
        'ask': 100.10
    }
    res_approved = executor.execute_order("TIGHT/USDC", "buy", amount_usd=10.0)
    assert res_approved is not None
    assert res_approved['side'] == 'buy'
    assert res_approved['price'] == 100.10

def test_btc_macro_crash_gate():
    """
    Test 4: Macro "BTC Crash Gate"
    Verifies that BTC dump candles (> 1.2% drop) or macro downtrend (ADX > 30 and Close < 20-EMA)
    activate MACRO_RISK_OFF and block altcoin Mean Reversion BUY signals.
    """
    strategy = Strategy()

    # Case A: Simulated BTC dump (-1.5% drop in previous 15m candle)
    btc_dump_df = pd.DataFrame({
        'open': [50000.0] * 28 + [50000.0, 49200.0],
        'close': [50000.0] * 28 + [49250.0, 49200.0], # iloc[-2] drop: (49250 - 50000) / 50000 = -1.5%
        'high': [50100.0] * 30,
        'low': [49000.0] * 30,
        'volume': [2000.0] * 30
    })

    risk_off, reason = check_btc_macro_gate(btc_dump_df)
    assert risk_off is True
    assert "BTC crash dump detected" in reason

    # Verify altcoin Mean Reversion BUY signal is blocked when macro_risk_off=True
    alt_df = pd.DataFrame({
        'close': [100.0] * 28 + [90.0, 90.0],
        'high': [102.0] * 28 + [92.0, 92.0],
        'low': [98.0] * 28 + [88.0, 88.0],
        'volume': [1000.0] * 30
    })
    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.bbands') as mock_bbands, \
         patch('src.strategy.ta.adx') as mock_adx:
         
        mock_rsi.return_value = pd.Series([50.0]*28 + [20.0, 20.0]) # Oversold RSI 20
        mock_atr.return_value = pd.Series([3.0]*30)
        mock_adx.return_value = pd.DataFrame({'ADX_14': [18.0]*30}) # Ranging
        mock_bbands.return_value = pd.DataFrame({
            'BBL_20_2.0': [95.0]*30,
            'BBM_20_2.0': [100.0]*30,
            'BBU_20_2.0': [105.0]*30,
            'BBB_20_2.0': [10.0]*30,
            'BBP_20_2.0': [0.5]*30
        })

        # When macro_risk_off is True -> BUY signal MUST BE BLOCKED
        res_blocked = strategy.evaluate("NEAR/USDC", alt_df, macro_risk_off=True)
        assert res_blocked['signal'] == 'HOLD'

        # When macro_risk_off is False -> BUY signal is allowed
        res_allowed = strategy.evaluate("NEAR/USDC", alt_df, macro_risk_off=False)
        assert res_allowed['signal'] == 'BUY'
        assert res_allowed['source'] == 'mean_reversion'

def test_dynamic_atr_targets_and_caps():
    """
    Test 5: Dynamic ATR targets in RiskEngine.
    Verifies dynamic multipliers (1.5x ATR SL, 2.0x / 3.5x ATR TP) and max loss cap (2.5%).
    """
    portfolio = Mock()
    risk_engine = RiskEngine(portfolio)

    entry = 100.0
    atr = 1.20 # 1.2% ATR

    # Mean Reversion: SL = max(100 - 1.5*1.2, 100*0.975) = max(98.2, 97.5) = 98.2
    # TP = 100 + 2.0 * 1.2 = 102.4
    targets_mr = risk_engine.calculate_dynamic_targets(entry, atr, strategy_source="mean_reversion")
    assert abs(targets_mr["stop_loss_price"] - 98.2) < 1e-4
    assert abs(targets_mr["take_profit_price"] - 102.4) < 1e-4

    # Momentum Breakout: SL = 98.2, TP = 100 + 3.5 * 1.2 = 104.2, Trail = 1.8
    targets_mom = risk_engine.calculate_dynamic_targets(entry, atr, strategy_source="breakout")
    assert abs(targets_mom["stop_loss_price"] - 98.2) < 1e-4
    assert abs(targets_mom["take_profit_price"] - 104.2) < 1e-4
    assert abs(targets_mom["trail_distance"] - 1.8) < 1e-4

    # Large ATR Cap: ATR = 5.0 (5.0%). 1.5 * 5 = 7.5% loss -> Capped at -2.5% max loss!
    targets_capped = risk_engine.calculate_dynamic_targets(entry, atr=5.0, strategy_source="breakout")
    assert targets_capped["stop_loss_price"] == 97.5  # Capped at -2.5%

def test_sqlite_wal_mode_and_concurrency():
    """
    Test 6: SQLite WAL mode, 10s busy timeout, and thread safety.
    """
    conn = database.get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"

    cursor.execute("PRAGMA busy_timeout;")
    timeout = cursor.fetchone()[0]
    assert timeout >= 10000
    conn.close()
