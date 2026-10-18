import os
import sys
import socket
import atexit
import time
import logging
import ccxt
from src.config import (
    SYMBOLS, 
    TIMEFRAME, 
    DRY_RUN, 
    DYNAMIC_SCREENER,
    USE_REVOLUT_SIGNALS,
    REVOLUT_MIN_SCORE,
    MIN_AI_CONFLUENCE_SCORE,
    MIN_ATR_RATIO,
    BTC_GATE_ENABLED
)
from src.market_data import MarketData
from src.strategy import Strategy
from src.execution import ExecutionManager
from src.visualization import plot_market_graph
from src.screener import TopMoverScreener
from src.position_tracker import PositionTracker
from src.portfolio import PortfolioManager
from src.risk import RiskEngine
from src.revolut_bridge import RevolutSignalBridge
from src import database

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MAX_RESTART_ATTEMPTS = 5
RESTART_BASE_DELAY = 30

_SOCKET_LOCK = None

def acquire_instance_lock(port: int = 58999, lock_path: str = "trading_bot.pid"):
    """
    Prevents multiple concurrent bot daemon instances using an OS-kernel-enforced TCP socket.
    Unlike fcntl flock on files, a TCP port bind cannot be bypassed by unlinking or file recreation.
    """
    global _SOCKET_LOCK
    if isinstance(port, str):
        lock_path = port
        port = 58999
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Intentionally do not set SO_REUSEADDR so OS kernel prevents any simultaneous bind
        s.bind(('127.0.0.1', port))
        s.listen(1)
        _SOCKET_LOCK = s
        
        # Write PID to file for easy process inspection
        try:
            with open(lock_path, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        def release_lock():
            global _SOCKET_LOCK
            try:
                if _SOCKET_LOCK:
                    _SOCKET_LOCK.close()
                    _SOCKET_LOCK = None
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except Exception:
                pass

        atexit.register(release_lock)
        logger.info(f"Acquired single-instance TCP socket lock on 127.0.0.1:{port} (PID: {os.getpid()})")
        return _SOCKET_LOCK
    except (OSError, socket.error) as e:
        logger.critical(f"FATAL: Port {port} already bound or another bot instance is running: {e}. Exiting to prevent collisions.")
        return None

def get_timeframe_seconds(timeframe: str) -> int:
    try:
        tf = timeframe.strip().lower()
        if tf.endswith('m'):
            return int(tf[:-1]) * 60
        elif tf.endswith('h'):
            return int(tf[:-1]) * 3600
        elif tf.endswith('d'):
            return int(tf[:-1]) * 86400
    except Exception:
        pass
    return 300  # Default fallback 5m

def main(attempt: int = 0):
    if attempt == 0:
        lock = acquire_instance_lock()
        if lock is None:
            sys.exit(1)
    if attempt >= MAX_RESTART_ATTEMPTS:
        logger.critical(f"Bot failed {MAX_RESTART_ATTEMPTS} consecutive times. Halting permanently.")
        return
    market_data = MarketData()
    strategy = Strategy()
    portfolio = PortfolioManager(market_data.exchange)
    risk_engine = RiskEngine(portfolio)
    executor = ExecutionManager(market_data.exchange, portfolio)
    tracker = PositionTracker()
    screener = TopMoverScreener(market_data.exchange)
    revolut_bridge = RevolutSignalBridge(exchange=market_data.exchange)

    quote_sample = SYMBOLS[0].split('/')[1] if SYMBOLS and '/' in SYMBOLS[0] else 'USDC'
    candle_period = get_timeframe_seconds(TIMEFRAME)

    logger.info("=" * 60)
    logger.info("Starting Advanced Algorithmic Trading Bot")
    logger.info(f"Mode: {'🟡 DRY RUN' if DRY_RUN else '🟢 LIVE TRADING'}")
    logger.info(f"Dynamic Screener: {'Enabled' if DYNAMIC_SCREENER else 'Disabled'}")
    logger.info(f"Revolut AI Bridge: {'Enabled' if USE_REVOLUT_SIGNALS else 'Disabled'}")
    logger.info(f"Timeframe: {TIMEFRAME} ({candle_period}s interval)")
    logger.info("=" * 60)
    
    # Record startup equity baseline so circuit breakers have a reference on first run
    startup_equity = portfolio.get_total_equity_usd()
    database.record_equity_snapshot(startup_equity)
    logger.info(f"Startup equity baseline recorded: ${startup_equity:.2f}")

    last_scan_time = 0
    first_scan = True
    try:
        while True:
            current_time = time.time()
            
            if database.get_kill_switch():
                logger.warning("🚨 EMERGENCY KILL-SWITCH ACTIVATED. Liquidating all positions and halting! 🚨")
                open_positions = tracker.get_open_positions()
                for symbol in open_positions:
                    logger.warning(f"Kill-Switch: Liquidating {symbol}...")
                    res = executor.execute_order(symbol, 'sell')
                    if res:
                        exit_price = res.get('price', 0.0)
                        tracker.record_exit(symbol, exit_price, "Kill-Switch Activated")
                logger.warning("All positions liquidated. Bot halted.")
                sys.exit(0)
            
            # --- Position Monitor (Runs every 10s) ---
            open_positions = tracker.get_open_positions()
            for symbol in open_positions:
                try:
                    ticker = market_data.exchange.fetch_ticker(symbol)
                    current_price = ticker.get('last', 0.0)
                    if current_price:
                        # 1. Trailing Stop / Hard Stop / Take Profit Exit
                        exit_check = tracker.check_position_exit(symbol, current_price)
                        if exit_check:
                            exit_type, reason, pnl_pct = exit_check
                            logger.warning(f"[{symbol}] {exit_type} TRIGGERED ({pnl_pct:+.2f}%): {reason}")
                            res = executor.execute_order(symbol, 'sell')
                            if res:
                                exit_price = res.get('price', current_price)
                                tracker.record_exit(symbol, exit_price, f"{exit_type}: {reason}")
                                continue

                        # 2. Revolut AI Invalidation Early-Warning Check
                        if USE_REVOLUT_SIGNALS:
                            is_emerg, emerg_reason = revolut_bridge.is_emergency_sell(symbol)
                            if is_emerg:
                                pos_data = open_positions.get(symbol, {})
                                entry_p = pos_data.get('entry_price', current_price)
                                current_pnl_pct = ((current_price - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                                # Exit immediately if losing or near breakeven (< +0.5%)
                                if current_pnl_pct < 0.5:
                                    logger.warning(f"[{symbol}] Revolut AI Invalidation Triggered ({current_pnl_pct:+.2f}%): {emerg_reason}")
                                    res = executor.execute_order(symbol, 'sell')
                                    if res:
                                        exit_price = res.get('price', current_price)
                                        tracker.record_exit(symbol, exit_price, f"Revolut AI Invalidation: {emerg_reason}")
                                        continue
                except Exception as e:
                    logger.error(f"Error fetching price for {symbol}: {e}")

            # Update open_positions after any sells
            open_positions = tracker.get_open_positions()

            # --- Ingestion Scanner (Runs immediately on startup, then aligned to candle close + 3s) ---
            current_aligned_cycle = current_time - (current_time % candle_period)
            target_scan_time = current_aligned_cycle + 3
            
            if first_scan or (current_time >= target_scan_time and last_scan_time < current_aligned_cycle):
                first_scan = False
                last_scan_time = current_time
                
                # Record portfolio state
                current_equity = portfolio.get_total_equity_usd()
                balances = portfolio.fetch_balances()
                database.save_portfolio_state(current_equity, balances)
                database.record_equity_snapshot(current_equity)

                if DYNAMIC_SCREENER:
                    active_symbols = screener.get_screened_pairs(SYMBOLS, max_extra=2)
                else:
                    active_symbols = list(SYMBOLS)

                # Ingest candidate crypto pairs from Revolut Signals Bot
                if USE_REVOLUT_SIGNALS:
                    rev_candidates = revolut_bridge.get_candidates(min_score=REVOLUT_MIN_SCORE, preferred_quote=quote_sample)
                    for cand in rev_candidates:
                        c_sym = cand["binance_symbol"]
                        if c_sym not in active_symbols:
                            active_symbols.append(c_sym)
                            logger.info(f"[Revolut Bridge] AI Candidate Ingested: {c_sym} (Score: {cand['composite_score']:+.2f})")

                # Evaluate Bitcoin Macro Market Gate
                btc_safe = True
                btc_gate_reason = ""
                if BTC_GATE_ENABLED:
                    try:
                        btc_sym = f"BTC/{quote_sample}"
                        btc_df = market_data.fetch_ohlcv(btc_sym, TIMEFRAME, limit=60)
                        if btc_df.empty:
                            btc_df = market_data.fetch_ohlcv("BTC/USDT", TIMEFRAME, limit=60)
                        btc_safe, btc_gate_reason = strategy.check_btc_macro_gate(btc_df)
                        if not btc_safe:
                            logger.warning(f"[Macro Market Gate] Altcoin BUYs suppressed: {btc_gate_reason}")
                        else:
                            logger.info(f"[Macro Market Gate] BTC status healthy: {btc_gate_reason}")
                    except Exception as e:
                        logger.warning(f"[Macro Market Gate] Failed to evaluate BTC macro gate: {e}")

                buy_signals = []

                for symbol in active_symbols:
                    try:
                        df = market_data.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
                    except Exception as e:
                        logger.error(f"Failed to fetch market data for {symbol}: {e}")
                        continue

                    if df.empty:
                        continue

                    current_price = float(df['close'].iloc[-1])
                    
                    # Revolut Intelligence
                    rev_score = 0.0
                    rev_summary = 'No AI coverage'
                    if USE_REVOLUT_SIGNALS:
                        rev_intel = revolut_bridge.get_sentiment(symbol)
                        if rev_intel:
                            rev_score = rev_intel.get('composite_score', 0.0)
                            rev_summary = rev_intel.get('summary', '')

                    # Strategy Evaluation (passes AI score to enable high-conviction AI Pullbacks)
                    eval_result = strategy.evaluate(symbol, df, rev_score=rev_score)
                    signal = eval_result.get('signal', 'HOLD')
                    source = eval_result.get('source', 'unknown')
                    priority_score = eval_result.get('priority_score', 0.0)
                    regime = strategy.get_regime(symbol)

                    # Hybrid AI Fusion (Revolut Intelligence)
                    if USE_REVOLUT_SIGNALS:
                        # Case A: Bearish Veto - Suppress BUYs when news/sentiment is heavily negative
                        if signal == 'BUY' and rev_score < -0.30:
                            logger.warning(
                                f"[{symbol}] Suppressing technical BUY: Revolut AI is bearish ({rev_score:+.2f}). Rationale: {rev_summary}"
                            )
                            signal = 'HOLD'

                        # Case B: Mandatory AI Confluence Bar - Eliminate chop trades lacking AI confirmation
                        elif signal == 'BUY' and rev_score < MIN_AI_CONFLUENCE_SCORE:
                            logger.warning(
                                f"[{symbol}] Suppressing technical BUY: Insufficient AI confluence ({rev_score:+.2f} < {MIN_AI_CONFLUENCE_SCORE:.2f}). Rationale: {rev_summary}"
                            )
                            signal = 'HOLD'

                        # Case C: High Conviction Convergent Boost - Strong agreement (score >= 0.35)
                        elif signal == 'BUY' and rev_score >= 0.35:
                            priority_score += rev_score * 20.0
                            if "revolut_ai" not in str(source):
                                source = f"{source}+revolut_ai"
                            logger.info(
                                f"[{symbol}] ⭐ High-Conviction AI Convergent BUY ({rev_score:+.2f}) -> Boosted Priority: {priority_score:.2f}"
                            )

                        # Case D: Moderate AI Confirmation (0.20 <= score < 0.35)
                        elif signal == 'BUY':
                            priority_score += rev_score * 10.0
                            if "revolut_ai" not in str(source):
                                source = f"{source}+revolut_ai"
                            logger.info(
                                f"[{symbol}] AI Confirmed BUY ({rev_score:+.2f}) -> Priority: {priority_score:.2f}"
                            )

                    # BTC Macro Gate Protection: Suppress altcoin buys if BTC market is dumping
                    if signal == 'BUY' and not btc_safe and "BTC/" not in symbol:
                        logger.warning(
                            f"[{symbol}] Suppressing BUY: BTC Macro Gate is active ({btc_gate_reason})"
                        )
                        signal = 'HOLD'
                    
                    logger.info(f"[{symbol}] Signal: {signal} | Regime: {regime} | Score: {priority_score:.2f}")
                    
                    # Cache market candles and indicators to SQLite for interactive dashboard
                    database.save_market_candles(symbol, TIMEFRAME, df, regime)
                    plot_market_graph(symbol, df, TIMEFRAME)

                    if signal == 'BUY':
                        if symbol not in open_positions:
                            buy_signals.append({
                                'symbol': symbol,
                                'current_price': current_price,
                                'source': source,
                                'priority_score': priority_score
                            })
                        else:
                            logger.info(f"[{symbol}] Already holding position, skipping BUY.")
                    elif signal == 'SELL':
                        if symbol in open_positions:
                            pos_info = open_positions.get(symbol, {})
                            pos_source = pos_info.get('source', 'unknown')
                            # Breakout positions seek trends; do not cut them at the middle Bollinger band
                            if source == 'mean_reversion' and pos_source == 'breakout':
                                logger.info(f"[{symbol}] Ignoring mean-reversion BBM sell for active breakout position.")
                                continue
                            logger.info(f"[{symbol}] SELL signal triggered by strategy indicator flip.")
                            res = executor.execute_order(symbol, 'sell')
                            if res:
                                exit_price = res.get('price', current_price)
                                tracker.record_exit(symbol, exit_price, f"Strategy indicator flip ({source})")
                                open_positions = tracker.get_open_positions()

                # Sort and execute BUY signals based on priority
                if buy_signals:
                    buy_signals = risk_engine.sort_buy_signals(buy_signals)
                    for b_sig in buy_signals:
                        symbol = b_sig['symbol']
                        current_price = b_sig['current_price']
                        source = b_sig['source']
                        active_count = len(tracker.get_open_positions())
                        pair_quote = symbol.split('/')[1] if '/' in symbol else quote_sample
                        
                        is_valid, reason, size_usd = risk_engine.validate_entry(symbol, active_count, pair_quote, current_price=current_price)
                        if is_valid:
                            res = executor.execute_order(symbol, 'buy', size_usd)
                            if res:
                                fill_price = res.get('price', current_price)
                                fill_amount = res.get('amount', 0.0)
                                quote = res.get('quote', pair_quote)
                                tracker.record_entry(symbol, fill_price, fill_amount, quote, source=source)
                        else:
                            logger.info(f"[{symbol}] BUY skipped: {reason}")
        
            time.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("Termination signal received. Shutting down gracefully.")
    except (ccxt.NetworkError, ccxt.ExchangeError) as e:
        delay = RESTART_BASE_DELAY * (2 ** attempt)  # Exponential backoff: 30s, 60s, 120s...
        logger.error(f"Exchange/Network error (attempt {attempt+1}/{MAX_RESTART_ATTEMPTS}): {e}. Restarting in {delay}s...")
        time.sleep(delay)
        main(attempt + 1)
    except Exception as e:
        logger.critical(f"Unexpected fatal error in main loop: {e}", exc_info=True)

if __name__ == "__main__":
    main()
