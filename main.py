import os
import sys
import socket
import atexit
import time
import logging
import asyncio
import ccxt
from src.config import (
    SYMBOLS, 
    TIMEFRAME, 
    DRY_RUN, 
    DYNAMIC_SCREENER,
    USE_REVOLUT_SIGNALS,
    REVOLUT_MIN_SCORE,
    MIN_ATR_RATIO
)
from src.market_data import MarketData
from src.strategy import Strategy, check_btc_macro_gate
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

async def monitor_open_positions_loop(market_data: MarketData, tracker: PositionTracker, executor: ExecutionManager, revolut_bridge: RevolutSignalBridge):
    """
    Decoupled 10-second position monitor loop.
    Evaluates Stop-Loss, Trailing Stop, Breakeven Ratchet, and Take-Profit rules asynchronously
    without being blocked by universe OHLCV ingestion.
    """
    logger.info("Started decoupled 10-second Position Monitor loop.")
    while True:
        try:
            if database.get_kill_switch():
                logger.warning("🚨 EMERGENCY KILL-SWITCH ACTIVATED. Liquidating all positions and halting! 🚨")
                open_positions = tracker.get_open_positions()
                for symbol in open_positions:
                    logger.warning(f"Kill-Switch: Liquidating {symbol}...")
                    res = await asyncio.to_thread(executor.execute_order, symbol, 'sell')
                    if res:
                        exit_price = res.get('price', 0.0)
                        tracker.record_exit(symbol, exit_price, "Kill-Switch Activated")
                logger.warning("All positions liquidated. Bot halted.")
                sys.exit(0)

            open_positions = tracker.get_open_positions()
            for symbol, pos in open_positions.items():
                try:
                    ticker = await asyncio.to_thread(market_data.exchange.fetch_ticker, symbol)
                    current_price = ticker.get('last') or ticker.get('close') or 0.0
                    if current_price:
                        # 1. Trailing Stop / Hard Stop / Breakeven Ratchet / Take Profit Exit
                        exit_check = tracker.check_position_exit(symbol, current_price)
                        if exit_check:
                            exit_type, reason, pnl_pct = exit_check
                            logger.warning(f"[{symbol}] {exit_type} TRIGGERED ({pnl_pct:+.2f}%): {reason}")
                            res = await asyncio.to_thread(executor.execute_order, symbol, 'sell')
                            if res:
                                exit_price = res.get('price', current_price)
                                tracker.record_exit(symbol, exit_price, f"{exit_type}: {reason}")
                                continue

                        # 2. Revolut AI Invalidation Early-Warning Check
                        if USE_REVOLUT_SIGNALS:
                            is_emerg, emerg_reason = revolut_bridge.is_emergency_sell(symbol)
                            if is_emerg:
                                entry_p = pos.get('entry_price', current_price)
                                current_pnl_pct = ((current_price - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                                # Exit immediately if losing or near breakeven (< +0.5%)
                                if current_pnl_pct < 0.5:
                                    logger.warning(f"[{symbol}] Revolut AI Invalidation Triggered ({current_pnl_pct:+.2f}%): {emerg_reason}")
                                    res = await asyncio.to_thread(executor.execute_order, symbol, 'sell')
                                    if res:
                                        exit_price = res.get('price', current_price)
                                        tracker.record_exit(symbol, exit_price, f"Revolut AI Invalidation: {emerg_reason}")
                                        continue
                except Exception as e:
                    logger.error(f"Error fetching ticker for {symbol} in monitor loop: {e}")
        except Exception as e:
            logger.error(f"Error in monitor_open_positions_loop: {e}")

        await asyncio.sleep(10)

def scan_universe_cycle(
    market_data: MarketData, 
    strategy: Strategy, 
    portfolio: PortfolioManager, 
    risk_engine: RiskEngine, 
    executor: ExecutionManager, 
    tracker: PositionTracker, 
    screener: TopMoverScreener, 
    revolut_bridge: RevolutSignalBridge, 
    quote_sample: str
):
    """
    Synchronous OHLCV universe ingestion cycle executed inside asyncio.to_thread
    so network latency never blocks the 10-second stop-loss monitor.
    """
    # Record portfolio state
    current_equity = portfolio.get_total_equity_usd()
    balances = portfolio.fetch_balances()
    database.save_portfolio_state(current_equity, balances)
    database.record_equity_snapshot(current_equity)

    # 1. Macro "BTC Crash Gate"
    btc_symbol = f"BTC/{quote_sample}"
    macro_risk_off = False
    macro_reason = ""
    try:
        btc_df = market_data.fetch_ohlcv(btc_symbol, TIMEFRAME, limit=50)
        macro_risk_off, macro_reason = check_btc_macro_gate(btc_df)
    except Exception:
        # Fallback to BTC/USDT if BTC/USDC fails
        try:
            btc_df = market_data.fetch_ohlcv("BTC/USDT", TIMEFRAME, limit=50)
            macro_risk_off, macro_reason = check_btc_macro_gate(btc_df)
        except Exception as e2:
            logger.warning(f"Could not fetch BTC candles for Macro Crash Gate: {e2}")

    if macro_risk_off:
        logger.warning(f"🚨 [MACRO BTC CRASH GATE ACTIVE] {macro_reason}. Blocking altcoin Mean Reversion BUY signals!")

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

    open_positions = tracker.get_open_positions()
    buy_signals = []

    for symbol in active_symbols:
        try:
            df = market_data.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
        except Exception as e:
            logger.error(f"Failed to fetch market data for {symbol}: {e}")
            continue

        if df.empty or len(df) < 28:
            continue

        # Macro risk-off applies to altcoins (not BTC itself)
        is_altcoin = not symbol.startswith("BTC/")
        is_macro_off_for_symbol = macro_risk_off and is_altcoin

        eval_result = strategy.evaluate(symbol, df, macro_risk_off=is_macro_off_for_symbol)
        signal = eval_result.get('signal', 'HOLD')
        source = eval_result.get('source', 'unknown')
        priority_score = eval_result.get('priority_score', 0.0)
        regime = strategy.get_regime(symbol)
        current_price = eval_result.get('live_price', float(df['close'].iloc[-1]))

        # Hybrid AI Fusion (Revolut Intelligence)
        if USE_REVOLUT_SIGNALS:
            rev_intel = revolut_bridge.get_sentiment(symbol)
            if rev_intel:
                rev_score = rev_intel.get('composite_score', 0.0)
                summary = rev_intel.get('summary', '')

                # Case A: Bearish Veto - Suppress BUYs when news/sentiment is heavily negative
                if signal == 'BUY' and rev_score < -0.30:
                    logger.warning(
                        f"[{symbol}] Suppressing technical BUY: Revolut AI is bearish ({rev_score:+.2f}). Rationale: {summary}"
                    )
                    signal = 'HOLD'

                # Case B: Convergent Boost - Both local technicals and Revolut AI agree
                elif signal == 'BUY' and rev_score >= 0.35:
                    priority_score += rev_score * 20.0
                    source = f"{source}+revolut_ai"
                    logger.info(
                        f"[{symbol}] ⭐ Convergent BUY Setup: Technicals + Revolut AI ({rev_score:+.2f}) -> Boosted Priority: {priority_score:.2f}"
                    )
        
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
                    'priority_score': priority_score,
                    'atr': eval_result.get('atr', 0.0),
                    'stop_loss_price': eval_result.get('stop_loss_price', 0.0),
                    'take_profit_price': eval_result.get('take_profit_price', 0.0)
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
                    tracker.record_entry(
                        symbol, 
                        fill_price, 
                        fill_amount, 
                        quote, 
                        source=source,
                        atr=b_sig.get('atr', 0.0),
                        stop_loss_price=b_sig.get('stop_loss_price', 0.0),
                        take_profit_price=b_sig.get('take_profit_price', 0.0)
                    )
            else:
                logger.info(f"[{symbol}] BUY skipped: {reason}")

async def scan_universe_loop(
    market_data: MarketData, 
    strategy: Strategy, 
    portfolio: PortfolioManager, 
    risk_engine: RiskEngine, 
    executor: ExecutionManager, 
    tracker: PositionTracker, 
    screener: TopMoverScreener, 
    revolut_bridge: RevolutSignalBridge, 
    candle_period: int, 
    quote_sample: str
):
    """
    Decoupled candle-aligned universe scanner loop.
    Spawns scan_universe_cycle inside asyncio.to_thread on each candle close (+3s).
    """
    first_scan = True
    last_scan_time = 0
    logger.info("Started decoupled candle-aligned Universe Scanner loop.")
    while True:
        try:
            current_time = time.time()
            current_aligned_cycle = current_time - (current_time % candle_period)
            target_scan_time = current_aligned_cycle + 3
            
            if first_scan or (current_time >= target_scan_time and last_scan_time < current_aligned_cycle):
                first_scan = False
                last_scan_time = current_time
                logger.info(f"Triggering scheduled universe scan cycle (time: {time.strftime('%H:%M:%S')})...")
                await asyncio.to_thread(
                    scan_universe_cycle,
                    market_data, strategy, portfolio, risk_engine, executor, tracker, screener, revolut_bridge, quote_sample
                )

            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in scan_universe_loop: {e}", exc_info=True)
            await asyncio.sleep(10)

async def async_main(
    market_data: MarketData, 
    strategy: Strategy, 
    portfolio: PortfolioManager, 
    risk_engine: RiskEngine, 
    executor: ExecutionManager, 
    tracker: PositionTracker, 
    screener: TopMoverScreener, 
    revolut_bridge: RevolutSignalBridge, 
    candle_period: int, 
    quote_sample: str
):
    """Runs position monitor and universe scanner concurrently in decoupled async tasks."""
    monitor_task = asyncio.create_task(
        monitor_open_positions_loop(market_data, tracker, executor, revolut_bridge)
    )
    scanner_task = asyncio.create_task(
        scan_universe_loop(
            market_data, strategy, portfolio, risk_engine, executor, tracker, screener, revolut_bridge, candle_period, quote_sample
        )
    )
    await asyncio.gather(monitor_task, scanner_task)

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

    try:
        asyncio.run(
            async_main(
                market_data, strategy, portfolio, risk_engine, executor, tracker, screener, revolut_bridge, candle_period, quote_sample
            )
        )
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
