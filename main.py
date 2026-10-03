import time
import logging
from src.config import (
    SYMBOLS, 
    TIMEFRAME, 
    DRY_RUN, 
    CAPITAL_PER_TRADE, 
    CAPITAL_EUR,
    DYNAMIC_SCREENER
)
from src.market_data import MarketData
from src.strategy import Strategy
from src.execution import ExecutionManager
from src.visualization import plot_market_graph
from src.screener import TopMoverScreener
from src.position_tracker import PositionTracker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    market_data = MarketData()
    strategy = Strategy()
    executor = ExecutionManager(market_data.exchange)
    tracker = PositionTracker()
    screener = TopMoverScreener(market_data.exchange)

    quote_sample = SYMBOLS[0].split('/')[1] if SYMBOLS and '/' in SYMBOLS[0] else 'USDC'
    logger.info("=" * 60)
    logger.info("Starting Advanced Algorithmic Trading Bot")
    logger.info(f"Mode: {'🟡 DRY RUN' if DRY_RUN else '🟢 LIVE TRADING'}")
    logger.info(f"Capital Per Trade: {CAPITAL_PER_TRADE} {quote_sample}")
    logger.info(f"Dynamic Screener: {'Enabled' if DYNAMIC_SCREENER else 'Disabled'}")
    logger.info(f"Timeframe: {TIMEFRAME}")
    logger.info("=" * 60)
    
    try:
        while True:
            # 1. Update active symbols via Dynamic Screener if enabled
            if DYNAMIC_SCREENER:
                active_symbols = screener.get_screened_pairs(SYMBOLS, max_extra=2)
            else:
                active_symbols = SYMBOLS

            for symbol in active_symbols:
                logger.info(f"--- Evaluating {symbol} ---")
                
                # Fetch recent candles
                try:
                    df = market_data.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
                except Exception as e:
                    logger.error(f"Failed to fetch market data for {symbol}: {e}")
                    continue

                if df.empty:
                    continue

                current_price = float(df['close'].iloc[-1])

                # 2. Check Dynamic Trailing Stop, Take-Profit, and Hard Stop on existing positions
                exit_check = tracker.check_position_exit(symbol, current_price)
                if exit_check:
                    exit_type, reason, pnl_pct = exit_check
                    logger.warning(f"[{symbol}] {exit_type} TRIGGERED ({pnl_pct:+.2f}%): {reason}")
                    res = executor.execute_order(symbol, 'sell')
                    if res:
                        tracker.record_exit(symbol, current_price, f"{exit_type}: {reason}")
                    continue

                # 3. Strategy Evaluation with ADX Regime
                signal = strategy.evaluate(symbol, df)
                regime = strategy.get_regime(symbol)
                logger.info(f"[{symbol}] Signal: {signal} | Regime: {regime}")
                
                # Generate candlestick chart for dashboard
                plot_market_graph(symbol, df, TIMEFRAME)
                
                # 4. Handle Signals
                open_positions = tracker.get_open_positions()

                if signal == 'BUY':
                    # Single-position capital protection rule
                    if open_positions:
                        holding_symbols = list(open_positions.keys())
                        logger.info(f"[{symbol}] BUY signal noted, but already holding open position in {holding_symbols}. Skipping to preserve capital.")
                    else:
                        res = executor.execute_order(symbol, 'buy')
                        if res:
                            fill_price = res.get('price', current_price)
                            fill_amount = res.get('amount', 0.0)
                            quote = res.get('quote', quote_sample)
                            tracker.record_entry(symbol, fill_price, fill_amount, quote)

                elif signal == 'SELL':
                    if symbol in open_positions:
                        logger.info(f"[{symbol}] SELL signal triggered by strategy indicator flip.")
                        res = executor.execute_order(symbol, 'sell')
                        if res:
                            tracker.record_exit(symbol, current_price, "Strategy indicator flip")
                    
            logger.info("Sleeping for 60 seconds before next evaluation cycle...")
            time.sleep(60)
            
    except KeyboardInterrupt:
        logger.info("Termination signal received. Shutting down gracefully.")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}")

if __name__ == "__main__":
    main()
