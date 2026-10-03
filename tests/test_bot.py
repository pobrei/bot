import pytest
import pandas as pd
from unittest.mock import Mock, patch

from src.strategy import Strategy
from src.execution import ExecutionManager

def make_series(last_prev, last_curr):
    return pd.Series([0.0] * 28 + [last_prev, last_curr])

@patch('src.strategy.ta.bbands')
@patch('src.strategy.ta.macd')
@patch('src.strategy.SentimentAnalyzer')
@patch('src.strategy.ta.ema')
@patch('src.strategy.ta.rsi')
def test_strategy_buy_signal(mock_rsi, mock_ema, mock_sentiment, mock_macd, mock_bbands):
    mock_sentiment_instance = Mock()
    mock_sentiment_instance.get_market_sentiment.return_value = 0.0
    mock_sentiment.return_value = mock_sentiment_instance
    mock_macd.return_value = None
    mock_bbands.return_value = None
    
    strategy = Strategy()
    
    # BUY CONDITION: Fast EMA crosses above Slow EMA AND RSI in [50, 65]
    def side_effect_ema(close, length):
        if length == 12:
            return make_series(10.0, 15.0)  # Fast EMA
        else:
            return make_series(12.0, 12.0)  # Slow EMA

    mock_ema.side_effect = side_effect_ema
    mock_rsi.return_value = make_series(50.0, 55.0) # RSI between 50 and 70
    
    df = pd.DataFrame({'close': [100.0] * 30})
    signal = strategy.evaluate('BTC/EUR', df)
    assert signal == 'BUY'

@patch('src.strategy.ta.bbands')
@patch('src.strategy.ta.macd')
@patch('src.strategy.SentimentAnalyzer')
@patch('src.strategy.ta.ema')
@patch('src.strategy.ta.rsi')
def test_strategy_sell_signal_rsi(mock_rsi, mock_ema, mock_sentiment, mock_macd, mock_bbands):
    mock_sentiment_instance = Mock()
    mock_sentiment_instance.get_market_sentiment.return_value = 0.0
    mock_sentiment.return_value = mock_sentiment_instance
    mock_macd.return_value = None
    mock_bbands.return_value = None
    
    strategy = Strategy()
    
    # SELL CONDITION: RSI > 75
    def side_effect_ema(close, length):
        return make_series(10.0, 15.0)

    mock_ema.side_effect = side_effect_ema
    mock_rsi.return_value = make_series(80.0, 80.0) # RSI > 75
    
    df = pd.DataFrame({'close': [100.0] * 30})
    signal = strategy.evaluate('BTC/EUR', df)
    assert signal == 'SELL'

@patch('src.strategy.ta.bbands')
@patch('src.strategy.ta.macd')
@patch('src.strategy.SentimentAnalyzer')
@patch('src.strategy.ta.ema')
@patch('src.strategy.ta.rsi')
def test_strategy_sell_signal_cross(mock_rsi, mock_ema, mock_sentiment, mock_macd, mock_bbands):
    mock_sentiment_instance = Mock()
    mock_sentiment_instance.get_market_sentiment.return_value = 0.0
    mock_sentiment.return_value = mock_sentiment_instance
    mock_macd.return_value = None
    mock_bbands.return_value = None
    
    strategy = Strategy()
    
    # SELL CONDITION: Fast EMA crosses below Slow EMA
    def side_effect_ema(close, length):
        if length == 12:
            return make_series(15.0, 10.0)  # Fast crosses below
        else:
            return make_series(12.0, 12.0)  # Slow
            
    mock_ema.side_effect = side_effect_ema
    mock_rsi.return_value = make_series(50.0, 50.0) # Neutral RSI
    
    df = pd.DataFrame({'close': [100.0] * 30})
    signal = strategy.evaluate('BTC/EUR', df)
    assert signal == 'SELL'

@patch('src.strategy.ta.bbands')
@patch('src.strategy.ta.macd')
@patch('src.strategy.SentimentAnalyzer')
@patch('src.strategy.ta.ema')
@patch('src.strategy.ta.rsi')
def test_strategy_hold_signal(mock_rsi, mock_ema, mock_sentiment, mock_macd, mock_bbands):
    mock_sentiment_instance = Mock()
    mock_sentiment_instance.get_market_sentiment.return_value = 0.0
    mock_sentiment.return_value = mock_sentiment_instance
    mock_macd.return_value = None
    mock_bbands.return_value = None
    
    strategy = Strategy()
    
    # HOLD CONDITION: No crosses, neutral RSI
    def side_effect_ema(close, length):
        if length == 12:
            return make_series(10.0, 10.0)
        else:
            return make_series(12.0, 12.0)
            
    mock_ema.side_effect = side_effect_ema
    mock_rsi.return_value = make_series(50.0, 50.0)
    
    df = pd.DataFrame({'close': [100.0] * 30})
    signal = strategy.evaluate('BTC/EUR', df)
    assert signal == 'HOLD'

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
    tracker.record_entry("BTC/USDC", entry_price=100.0, amount=0.05, quote="USDC")
    
    # 2. Price rises to $101 (+1.0%) -> trailing not yet active (needs +1.5%)
    assert tracker.check_position_exit("BTC/USDC", 101.0) is None
    
    # 3. Price peaks at $103 (+3.0%) -> activates trailing stop
    assert tracker.check_position_exit("BTC/USDC", 103.0) is None
    pos = tracker.get_open_position("BTC/USDC")
    assert pos["trailing_active"] is True
    assert pos["highest_price"] == 103.0
    
    # 4. Price retraces 1.2% from peak (to 101.5, below 103 * 0.99 = 101.97) -> triggers TRAILING_STOP
    exit_check = tracker.check_position_exit("BTC/USDC", 101.5)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "TRAILING_STOP"
    assert pnl_pct > 1.0  # Profitable exit
    
    # 5. Record exit and verify history
    tracker.record_exit("BTC/USDC", 101.5, "Trailing stop hit")
    assert tracker.get_open_position("BTC/USDC") is None
    history = tracker.get_trade_history()
    assert len(history) == 1
    assert history[0]["symbol"] == "BTC/USDC"
    assert history[0]["realized_pnl"] > 0

def test_position_tracker_hard_stop_loss(tmp_path):
    from src.position_tracker import PositionTracker
    pos_file = str(tmp_path / "test_positions.json")
    hist_file = str(tmp_path / "test_history.json")
    
    tracker = PositionTracker(pos_file, hist_file)
    tracker.record_entry("ETH/USDC", entry_price=100.0, amount=0.05, quote="USDC")
    
    # Drops 2.6% (below -2.5% HARD_STOP_LOSS)
    exit_check = tracker.check_position_exit("ETH/USDC", 97.4)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "HARD_STOP_LOSS"
    assert pnl_pct <= -2.5

def test_position_tracker_take_profit(tmp_path):
    from src.position_tracker import PositionTracker
    pos_file = str(tmp_path / "test_positions.json")
    hist_file = str(tmp_path / "test_history.json")
    
    tracker = PositionTracker(pos_file, hist_file)
    tracker.record_entry("SOL/USDC", entry_price=100.0, amount=0.05, quote="USDC")
    
    # Surges +5.5% (above +5.0% TAKE_PROFIT_TARGET)
    exit_check = tracker.check_position_exit("SOL/USDC", 105.5)
    assert exit_check is not None
    exit_type, reason, pnl_pct = exit_check
    assert exit_type == "TAKE_PROFIT"
    assert pnl_pct >= 5.0

def test_top_mover_screener():
    from src.screener import TopMoverScreener
    mock_exchange = Mock()
    mock_exchange.fetch_tickers.return_value = {
        'BTC/USDC': {'quoteVolume': 1000000, 'percentage': 1.0},
        'SAND/USDC': {'quoteVolume': 500000, 'percentage': 25.0},
        'LOWVOL/USDC': {'quoteVolume': 1000, 'percentage': 50.0}, # Too low volume
        'BEAR/USDC': {'quoteVolume': 200000, 'percentage': 30.0},  # Leveraged token
        'ETH/USDC': {'quoteVolume': 800000, 'percentage': 0.5},
    }
    
    screener = TopMoverScreener(mock_exchange, cache_duration_seconds=10)
    base_basket = ['BTC/USDC', 'ETH/USDC']
    
    screened = screener.get_screened_pairs(base_basket, max_extra=1)
    # SAND/USDC should be added as top mover
    assert 'SAND/USDC' in screened
    assert 'BEAR/USDC' not in screened
    assert 'LOWVOL/USDC' not in screened
    assert 'BTC/USDC' in screened


