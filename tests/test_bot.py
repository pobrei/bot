import pytest
import pandas as pd
from unittest.mock import Mock, patch

from src.strategy import Strategy
from src.execution import ExecutionManager

def make_series(last_prev, last_curr):
    return pd.Series([0.0] * 28 + [last_prev, last_curr])

def test_strategy_buy_signal_mean_reversion():
    strategy = Strategy()
    # To get BUY in Mean Reversion:
    # ATR/close >= 0.015 -> close=100, high=102, low=98, ATR approx 4.0
    # ADX < 25 (default is RANGING if no ADX or ADX < 25)
    # close <= bbl (lower band)
    # RSI <= 30
    df = pd.DataFrame({
        'close': [100.0] * 29 + [90.0],
        'high': [102.0] * 29 + [92.0],
        'low': [98.0] * 29 + [88.0],
        'volume': [1000.0] * 30
    })
    # We will mock pandas_ta_classic within Strategy since it's hard to generate perfect data for ta.bbands and ta.rsi
    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.bbands') as mock_bbands, \
         patch('src.strategy.ta.adx') as mock_adx:
         
        mock_rsi.return_value = pd.Series([50.0]*29 + [20.0]) # RSI=20
        mock_atr.return_value = pd.Series([2.0]*30) # ATR=2.0 -> 2.0/90.0 >= 0.015
        mock_adx.return_value = pd.DataFrame({'ADX_14': [20.0]*30}) # ADX=20
        # BBands: BBL, BBM, BBU. We need close <= BBL -> 90 <= BBL. So BBL=95
        mock_bbands.return_value = pd.DataFrame({
            'BBL_20_2.0': [95.0]*30,
            'BBM_20_2.0': [100.0]*30,
            'BBU_20_2.0': [105.0]*30,
            'BBB_20_2.0': [10.0]*30,
            'BBP_20_2.0': [0.5]*30
        })

        signal = strategy.evaluate('BTC/EUR', df)
        assert signal['signal'] == 'BUY'
        assert signal['source'] == 'mean_reversion'

def test_strategy_sell_signal_mean_reversion():
    strategy = Strategy()
    df = pd.DataFrame({
        'close': [100.0] * 29 + [105.0],
        'high': [102.0] * 29 + [107.0],
        'low': [98.0] * 29 + [103.0],
        'volume': [1000.0] * 30
    })
    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.bbands') as mock_bbands, \
         patch('src.strategy.ta.adx') as mock_adx:
         
        mock_rsi.return_value = pd.Series([50.0]*30)
        mock_atr.return_value = pd.Series([2.0]*30) # ATR=2.0 -> 2.0/105.0 >= 0.015
        mock_adx.return_value = pd.DataFrame({'ADX_14': [20.0]*30})
        # BBands: close >= BBM -> 105 >= BBM. So BBM=100
        mock_bbands.return_value = pd.DataFrame({
            'BBL_20_2.0': [95.0]*30,
            'BBM_20_2.0': [100.0]*30,
            'BBU_20_2.0': [105.0]*30,
            'BBB_20_2.0': [10.0]*30,
            'BBP_20_2.0': [0.5]*30
        })

        signal = strategy.evaluate('BTC/EUR', df)
        assert signal['signal'] == 'SELL'

def test_strategy_buy_signal_breakout():
    strategy = Strategy()
    # close > Donchian_High_20, vol_ratio >= 2.0, close_pos >= 0.75, ADX >= 25
    df = pd.DataFrame({
        'close': [100.0] * 29 + [110.0],
        'high': [102.0] * 29 + [110.0], # close_pos = (110 - 100) / (110 - 100) = 1.0
        'low': [98.0] * 29 + [100.0],
        'volume': [1000.0] * 29 + [3000.0] # vol_ratio = 3000 / avg(1000) = 3.0
    })
    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.bbands') as mock_bbands, \
         patch('src.strategy.ta.adx') as mock_adx:
         
        mock_rsi.return_value = pd.Series([50.0]*30)
        mock_atr.return_value = pd.Series([5.0]*30)
        mock_adx.return_value = pd.DataFrame({'ADX_14': [30.0]*30}) # Trending
        mock_bbands.return_value = None # Doesn't matter

        signal = strategy.evaluate('BTC/EUR', df)
        assert signal['signal'] == 'BUY'
        assert signal['source'] == 'breakout'

def test_strategy_hold_signal():
    strategy = Strategy()
    df = pd.DataFrame({
        'close': [100.0] * 30,
        'high': [102.0] * 30,
        'low': [98.0] * 30,
        'volume': [1000.0] * 30
    })
    with patch('src.strategy.ta.rsi') as mock_rsi, \
         patch('src.strategy.ta.atr') as mock_atr, \
         patch('src.strategy.ta.bbands') as mock_bbands, \
         patch('src.strategy.ta.adx') as mock_adx:
         
        mock_rsi.return_value = pd.Series([50.0]*30)
        mock_atr.return_value = pd.Series([2.0]*30) 
        mock_adx.return_value = pd.DataFrame({'ADX_14': [20.0]*30})
        mock_bbands.return_value = pd.DataFrame({
            'BBL_20_2.0': [95.0]*30,
            'BBM_20_2.0': [101.0]*30,
            'BBU_20_2.0': [105.0]*30,
            'BBB_20_2.0': [10.0]*30,
            'BBP_20_2.0': [0.5]*30
        })

        signal = strategy.evaluate('BTC/EUR', df)
        assert signal['signal'] == 'HOLD'

def test_minimum_notional_gate(caplog):
    mock_exchange = Mock()
    mock_exchange.market.return_value = {'limits': {'cost': {'min': 5.0}}}
    mock_exchange.fetch_ticker.return_value = {'last': 50000.0}
    mock_exchange.amount_to_precision.return_value = '0.0001'

    executor = ExecutionManager(mock_exchange)
    executor.dry_run = False
    
    # Reject when < 5
    executor.capital = 4.9
    executor.execute_order('BTC/EUR', 'buy')
    assert "Capital 4.9 EUR is below minimum order cost 5.0 EUR. Order rejected." in caplog.text
    mock_exchange.create_market_order.assert_not_called()
    
    caplog.clear()

    # Accept when >= 5
    executor.capital = 5.0
    executor.execute_order('BTC/EUR', 'buy')
    mock_exchange.create_market_order.assert_called_once_with('BTC/EUR', 'buy', 0.0001)

def test_dry_run_integrity(caplog):
    import logging
    caplog.set_level(logging.INFO)
    mock_exchange = Mock()
    mock_exchange.market.return_value = {'limits': {'cost': {'min': 5.0}}}
    mock_exchange.fetch_ticker.return_value = {'last': 50000.0}
    mock_exchange.amount_to_precision.return_value = '0.0001'
    
    executor = ExecutionManager(mock_exchange)
    executor.capital = 10.0
    executor.dry_run = True # Dry run enabled
    
    executor.execute_order('BTC/EUR', 'buy')
    
    # Ensure no API request was made
    mock_exchange.create_market_order.assert_not_called()
    
    # Ensure it logged properly
    assert "[DRY RUN] BUY 0.0001 BTC/EUR @ 50000.0" in caplog.text

def test_usdt_and_usd_order_execution(caplog):
    import logging
    caplog.set_level(logging.INFO)
    mock_exchange = Mock()
    mock_exchange.market.return_value = {'limits': {'cost': {'min': 5.0}}}
    mock_exchange.fetch_ticker.return_value = {'last': 100.0}
    mock_exchange.amount_to_precision.return_value = '0.05'
    
    executor = ExecutionManager(mock_exchange)
    executor.capital = 5.0
    executor.dry_run = True
    
    # Test USDT pair
    executor.execute_order('BTC/USDT', 'buy')
    assert "[DRY RUN] BUY 0.05 BTC/USDT @ 100.0 | Value: 5.0 USDT" in caplog.text
    
    caplog.clear()
    
    # Test USD pair
    executor.execute_order('ETH/USD', 'sell')
    assert "[DRY RUN] SELL 0.05 ETH/USD @ 100.0 | Value: 5.0 USD" in caplog.text

def test_position_tracker_trailing_stop(tmp_path):
    from src.position_tracker import PositionTracker
    pos_file = str(tmp_path / "test_positions.json")
    hist_file = str(tmp_path / "test_history.json")
    
    tracker = PositionTracker(pos_file, hist_file)
    
    # 1. Entry at $100
    tracker.record_entry("BTC/USDC", entry_price=100.0, amount=0.05, quote="USDC", source="breakout")
    
    # 3. Price peaks at $103 (+3.0%) -> activates trailing stop (internally)
    assert tracker.check_position_exit("BTC/USDC", 103.0) is None
    
    # 4. Price falls to $101.0, which is below 103 * (1 - 0.012) = 101.764
    exit_result = tracker.check_position_exit("BTC/USDC", 101.0)
    assert exit_result is not None
    assert exit_result[0] == "TRAILING_STOP"
    
    # 5. Record exit and verify history
    tracker.record_exit("BTC/USDC", 101.0, "Trailing stop hit")
    assert tracker.get_open_position("BTC/USDC") is None
    history = tracker.get_trade_history()
    assert len(history) >= 1
    assert history[-1]["symbol"] == "BTC/USDC"
    assert history[-1]["realized_pnl"] > 0

def test_position_tracker_hard_stop_loss(tmp_path):
    from src.position_tracker import PositionTracker
    pos_file = str(tmp_path / "test_positions.json")
    hist_file = str(tmp_path / "test_history.json")
    
    tracker = PositionTracker(pos_file, hist_file)
    tracker.record_entry("ETH/USDC", entry_price=100.0, amount=0.05, quote="USDC", source="mean_reversion")
    
    # Drops 1.5% (below -1.0% HARD_STOP_LOSS for mean_reversion)
    exit_check = tracker.check_position_exit("ETH/USDC", 98.5)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "HARD_STOP_LOSS"
    assert pnl_pct <= -1.5

def test_position_tracker_take_profit(tmp_path):
    from src.position_tracker import PositionTracker
    pos_file = str(tmp_path / "test_positions.json")
    hist_file = str(tmp_path / "test_history.json")
    
    tracker = PositionTracker(pos_file, hist_file)
    tracker.record_entry("SOL/USDC", entry_price=100.0, amount=0.05, quote="USDC", source="mean_reversion")
    
    # Surges +2.6% (above +2.5% TAKE_PROFIT_TARGET for mean_reversion)
    exit_check = tracker.check_position_exit("SOL/USDC", 102.6)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "TAKE_PROFIT"
    assert pnl_pct >= 2.5

def test_top_mover_screener():
    from src.screener import TopMoverScreener
    mock_exchange = Mock()
    mock_exchange.fetch_tickers.return_value = {
        'BTC/USDC': {'quoteVolume': 10000000, 'percentage': 1.0},
        'SAND/USDC': {'quoteVolume': 8000000, 'percentage': 25.0},
        'LOWVOL/USDC': {'quoteVolume': 1000, 'percentage': 50.0}, # Too low volume
        'BEAR/USDC': {'quoteVolume': 200000, 'percentage': 30.0},  # Leveraged token
        'ETH/USDC': {'quoteVolume': 8000000, 'percentage': 0.5},
    }
    
    screener = TopMoverScreener(mock_exchange, cache_duration_seconds=10)
    base_basket = ['BTC/USDC', 'ETH/USDC']
    
    screened = screener.get_screened_pairs(base_basket, max_extra=1)
    # SAND/USDC should be added as top mover
    assert 'SAND/USDC' in screened
    assert 'BEAR/USDC' not in screened
    assert 'LOWVOL/USDC' not in screened
    assert 'BTC/USDC' in screened

def test_trailing_stop_not_triggered_below_activation(tmp_path):
    from src.position_tracker import PositionTracker
    tracker = PositionTracker()
    
    # Entry at $100
    tracker.record_entry("ADA/USDC", entry_price=100.0, amount=1.0, quote="USDC", source="breakout")
    
    # 1. Price wiggles up slightly to $100.30 (+0.3%), not enough to arm (+1.5%)
    assert tracker.check_position_exit("ADA/USDC", 100.30) is None
    
    # 2. Price falls to $99.10 (-0.90%). 
    # Should NOT trigger trailing stop because it never armed, and has not hit hard stop (-2.5%)
    assert tracker.check_position_exit("ADA/USDC", 99.10) is None
    
    # 3. Price surges to $102.50 (+2.50%) -> arms trailing stop
    assert tracker.check_position_exit("ADA/USDC", 102.50) is None
    
    # 4. Price falls back to $101.20 (> 1.0% below 102.50 peak) -> Trailing stop hits with profit!
    exit_check = tracker.check_position_exit("ADA/USDC", 101.20)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "TRAILING_STOP"
    assert pnl_pct > 0  # Locked in green profit!

def test_instance_locking(tmp_path):
    from main import acquire_instance_lock
    import main
    
    # Reset any existing socket lock
    if main._SOCKET_LOCK:
        try:
            main._SOCKET_LOCK.close()
        except Exception:
            pass
        main._SOCKET_LOCK = None

    lock_pid_path = str(tmp_path / "test_bot.pid")
    lock1 = acquire_instance_lock(port=58998, lock_path=lock_pid_path)
    assert lock1 is not None
    
    # Second attempt on same port must be rejected
    lock2 = acquire_instance_lock(port=58998, lock_path=lock_pid_path)
    assert lock2 is None
    
    # Cleanup first lock
    lock1.close()
    main._SOCKET_LOCK = None



