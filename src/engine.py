import os
import sys
import time
import socket
import atexit
import signal
import logging
from typing import Optional, List, Dict, Any
import ccxt

from .config import (
    SYMBOLS, 
    TIMEFRAME, 
    DRY_RUN, 
    DYNAMIC_SCREENER,
    USE_REVOLUT_SIGNALS,
    REVOLUT_MIN_SCORE,
    MIN_AI_CONFLUENCE_SCORE,
    BTC_GATE_ENABLED,
    MICRO_MOMENTUM_ENABLED,
    MICRO_TIMEFRAME,
    MICRO_SCAN_INTERVAL,
    MICRO_VOLUME_SURGE_RATIO,
    PARTIAL_TP_RATIO
)
from .market_data import MarketData
from .strategy import Strategy
from .execution import ExecutionManager
from .visualization import plot_market_graph
from .screener import TopMoverScreener
from .position_tracker import PositionTracker
from .portfolio import PortfolioManager
from .risk import RiskEngine
from .revolut_bridge import RevolutSignalBridge
from . import database

logger = logging.getLogger("TradingEngine")

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

class TradingEngine:
    """
    Autonomous Trading Engine orchestrating market scanning, risk management,
    Revolut AI confluence, and order execution.
    """
    def __init__(self, dry_run: Optional[bool] = None, lock_port: int = 58999, skip_lock: bool = False):
        database.init_db()
        self.lock_port = lock_port
        self.skip_lock = skip_lock
        self.socket_lock: Optional[socket.socket] = None
        self.is_running = False

        # Initialize core modular components
        self.market_data = MarketData()
        self.strategy = Strategy()
        self.portfolio = PortfolioManager(self.market_data.exchange)
        self.risk_engine = RiskEngine(self.portfolio)
        self.executor = ExecutionManager(self.market_data.exchange, self.portfolio)
        self.tracker = PositionTracker()
        self.screener = TopMoverScreener(self.market_data.exchange)
        self.revolut_bridge = RevolutSignalBridge(exchange=self.market_data.exchange)

        if dry_run is not None:
            self.executor.dry_run = dry_run

        self.quote_sample = SYMBOLS[0].split('/')[1] if SYMBOLS and '/' in SYMBOLS[0] else 'USDC'
        self.candle_period = get_timeframe_seconds(TIMEFRAME)

        # Timers
        self.last_scan_time = 0.0
        self.last_micro_scan_time = 0.0
        self.first_scan = True

        # State cache
        self.btc_safe = True
        self.btc_gate_reason = "Initialized OK"
        self.active_universe: List[str] = list(SYMBOLS)

    def acquire_instance_lock(self, lock_path: str = "trading_bot.pid") -> bool:
        """
        Guarantees single daemon process execution via OS TCP port bind.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(('127.0.0.1', self.lock_port))
            s.listen(1)
            self.socket_lock = s
            
            try:
                with open(lock_path, "w") as f:
                    f.write(str(os.getpid()))
            except Exception:
                pass

            def release():
                if self.socket_lock:
                    try:
                        self.socket_lock.close()
                    except Exception:
                        pass
                    self.socket_lock = None
                if os.path.exists(lock_path):
                    try:
                        os.remove(lock_path)
                    except Exception:
                        pass

            atexit.register(release)
            logger.info(f"Acquired single-instance TCP socket lock on 127.0.0.1:{self.lock_port} (PID: {os.getpid()})")
            return True
        except (OSError, socket.error) as e:
            logger.critical(f"FATAL: Port {self.lock_port} already bound or another bot instance is running: {e}. Exiting.")
            return False

    def sync_portfolio_state(self):
        """
        Calculates and stores both simulated paper equity and live Binance wallet state in SQLite.
        Guarantees clean, non-doubled portfolio data for dashboard rendering.
        """
        try:
            paper_equity = self.portfolio.get_total_equity_usd()
            paper_balances = self.portfolio.fetch_balances()
            
            # Fetch real Binance live balance if keys are configured
            live_equity = None
            live_balances = None
            try:
                live_balances = self.portfolio.fetch_live_balances()
                if live_balances:
                    live_equity = self.portfolio.get_total_equity_usd(live_balances)
            except Exception as e:
                logger.debug(f"Live balance synchronization notice: {e}")

            database.save_portfolio_state(
                total_equity=paper_equity,
                balances=paper_balances,
                live_equity=live_equity,
                live_balances=live_balances
            )
            database.record_equity_snapshot(paper_equity)
        except Exception as e:
            logger.error(f"Error syncing portfolio state: {e}")

    def check_emergency_kill_switch(self) -> bool:
        """
        Checks if kill switch is activated in DB. If active, liquidates all positions
        and puts engine in standby mode until resumed via UI or DB.
        """
        if database.get_kill_switch():
            open_positions = self.tracker.get_open_positions()
            if open_positions:
                logger.warning("🚨 EMERGENCY KILL-SWITCH ACTIVATED. Liquidating all positions! 🚨")
                for symbol in open_positions:
                    logger.warning(f"Kill-Switch: Liquidating {symbol}...")
                    res = self.executor.execute_order(symbol, 'sell')
                    if res:
                        exit_price = res.get('price', 0.0)
                        self.tracker.record_exit(symbol, exit_price, "Kill-Switch Activated")
                self.sync_portfolio_state()
                logger.warning("All positions liquidated. Engine in standby mode.")
            return True
        return False

    def monitor_open_positions(self):
        """
        Evaluates open positions every 10 seconds:
        - Trailing stop / partial take-profit / hard stop loss / stagnation timeout
        - Revolut AI emergency invalidation early exit
        """
        open_positions = self.tracker.get_open_positions()
        if not open_positions:
            return

        for symbol in list(open_positions.keys()):
            try:
                ticker = self.market_data.exchange.fetch_ticker(symbol)
                current_price = float(ticker.get('last') or ticker.get('close') or 0.0)
                if not current_price or current_price <= 0:
                    continue

                # 1. PositionTracker rule checks (Trailing stop, TP, Partial TP, Stop Loss, Stagnation)
                exit_check = self.tracker.check_position_exit(symbol, current_price)
                if exit_check:
                    exit_type, reason, pnl_pct = exit_check
                    if exit_type == "PARTIAL_TAKE_PROFIT":
                        logger.warning(f"[{symbol}] {exit_type} TRIGGERED ({pnl_pct:+.2f}%): {reason}")
                        pos_info = open_positions.get(symbol, {})
                        full_amount = pos_info.get('amount', 0.0)
                        partial_sell_amount = full_amount * PARTIAL_TP_RATIO
                        res = self.executor.execute_order(symbol, 'sell', amount=partial_sell_amount)
                        if res and res.get('amount', 0.0) > 0:
                            sold_amt = res.get('amount', partial_sell_amount)
                            exit_p = res.get('price', current_price)
                            self.tracker.record_partial_exit(symbol, exit_p, sold_amt, reason)
                        continue
                    else:
                        logger.warning(f"[{symbol}] {exit_type} TRIGGERED ({pnl_pct:+.2f}%): {reason}")
                        res = self.executor.execute_order(symbol, 'sell')
                        if res:
                            exit_price = res.get('price', current_price)
                            self.tracker.record_exit(symbol, exit_price, f"{exit_type}: {reason}")
                        continue

                # 2. Revolut AI Invalidation Early-Warning Check
                if USE_REVOLUT_SIGNALS:
                    is_emerg, emerg_reason = self.revolut_bridge.is_emergency_sell(symbol)
                    if is_emerg:
                        pos_data = open_positions.get(symbol, {})
                        entry_p = pos_data.get('entry_price', current_price)
                        current_pnl_pct = ((current_price - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                        if current_pnl_pct < 0.5:
                            logger.warning(f"[{symbol}] Revolut AI Invalidation ({current_pnl_pct:+.2f}%): {emerg_reason}")
                            res = self.executor.execute_order(symbol, 'sell')
                            if res:
                                exit_price = res.get('price', current_price)
                                self.tracker.record_exit(symbol, exit_price, f"Revolut AI Invalidation: {emerg_reason}")
                                continue
            except Exception as e:
                logger.error(f"Error monitoring open position {symbol}: {e}")

    def evaluate_btc_macro_gate(self):
        """
        Evaluates Bitcoin macro health (EMA 50 / 20 EMA trend + 2-bar/4-bar fast drop check).
        """
        if not BTC_GATE_ENABLED:
            self.btc_safe = True
            self.btc_gate_reason = "BTC Gate disabled"
            return

        try:
            btc_sym = f"BTC/{self.quote_sample}"
            btc_df = self.market_data.fetch_ohlcv(btc_sym, TIMEFRAME, limit=60)
            if btc_df.empty:
                btc_df = self.market_data.fetch_ohlcv("BTC/USDT", TIMEFRAME, limit=60)
            self.btc_safe, self.btc_gate_reason = self.strategy.check_btc_macro_gate(btc_df)
            if not self.btc_safe:
                logger.warning(f"[Macro Market Gate] Altcoin BUYs suppressed: {self.btc_gate_reason}")
            else:
                logger.info(f"[Macro Market Gate] BTC status healthy: {self.btc_gate_reason}")
        except Exception as e:
            logger.warning(f"[Macro Market Gate] Failed to evaluate BTC macro gate: {e}")

    def run_candle_scan_cycle(self):
        """
        Primary strategy evaluation cycle aligned to candle period close:
        - Updates portfolio state in SQLite
        - Screens dynamic mover pairs and ingests Revolut AI candidates
        - Evaluates Bitcoin macro market gate
        - Runs multi-factor strategy on all pairs
        - Applies Revolut AI sentiment and technical veto filters
        - Sorts and executes high-priority BUY signals
        """
        self.sync_portfolio_state()

        # Update active universe
        if DYNAMIC_SCREENER:
            self.active_universe = self.screener.get_screened_pairs(SYMBOLS, max_extra=2)
        else:
            self.active_universe = list(SYMBOLS)

        # Ingest Revolut AI candidate pairs
        if USE_REVOLUT_SIGNALS:
            rev_candidates = self.revolut_bridge.get_candidates(min_score=REVOLUT_MIN_SCORE, preferred_quote=self.quote_sample)
            for cand in rev_candidates:
                c_sym = cand["binance_symbol"]
                if c_sym not in self.active_universe:
                    self.active_universe.append(c_sym)
                    logger.info(f"[Revolut Bridge] AI Candidate Ingested: {c_sym} (Score: {cand['composite_score']:+.2f})")

        # Evaluate BTC Macro Gate
        self.evaluate_btc_macro_gate()

        buy_signals = []
        open_positions = self.tracker.get_open_positions()

        for symbol in self.active_universe:
            try:
                df = self.market_data.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
            except Exception as e:
                logger.error(f"Failed to fetch market data for {symbol}: {e}")
                continue

            if df.empty:
                continue

            current_price = float(df['close'].iloc[-1])
            
            # Fetch Revolut Intelligence
            rev_score = 0.0
            rev_tech = 0.0
            rev_summary = 'No AI coverage'
            if USE_REVOLUT_SIGNALS:
                rev_intel = self.revolut_bridge.get_sentiment(symbol)
                if rev_intel:
                    rev_score = rev_intel.get('composite_score', 0.0)
                    rev_tech = rev_intel.get('tech_score', 0.0)
                    rev_summary = rev_intel.get('summary', '')

            # Strategy Evaluation
            eval_result = self.strategy.evaluate(symbol, df, rev_score=rev_score)
            signal = eval_result.get('signal', 'HOLD')
            source = eval_result.get('source', 'unknown')
            priority_score = eval_result.get('priority_score', 0.0)
            regime = self.strategy.get_regime(symbol)

            # Revolut AI Fusion & Veto Logic
            if USE_REVOLUT_SIGNALS:
                # Veto: Heavily bearish sentiment or negative technical momentum
                if signal == 'BUY' and (rev_score < -0.30 or rev_tech < -0.20):
                    logger.warning(
                        f"[{symbol}] Suppressing BUY: Revolut AI bearish (comp: {rev_score:+.2f}, tech: {rev_tech:+.2f}). Rationale: {rev_summary}"
                    )
                    signal = 'HOLD'
                # Mandatory AI Confluence Bar
                elif signal == 'BUY' and rev_score < MIN_AI_CONFLUENCE_SCORE:
                    logger.warning(
                        f"[{symbol}] Suppressing BUY: Insufficient AI confluence ({rev_score:+.2f} < {MIN_AI_CONFLUENCE_SCORE:.2f})"
                    )
                    signal = 'HOLD'
                # High Conviction Convergent Boost
                elif signal == 'BUY' and rev_score >= 0.35:
                    priority_score += rev_score * 20.0
                    if "revolut_ai" not in str(source):
                        source = f"{source}+revolut_ai"
                    logger.info(f"[{symbol}] ⭐ High-Conviction AI Convergent BUY ({rev_score:+.2f}) -> Priority: {priority_score:.2f}")
                # Moderate AI Confirmation
                elif signal == 'BUY':
                    priority_score += rev_score * 10.0
                    if "revolut_ai" not in str(source):
                        source = f"{source}+revolut_ai"
                    logger.info(f"[{symbol}] AI Confirmed BUY ({rev_score:+.2f}) -> Priority: {priority_score:.2f}")

            # BTC Macro Gate Protection
            if signal == 'BUY' and not self.btc_safe and "BTC/" not in symbol:
                logger.warning(f"[{symbol}] Suppressing BUY: BTC Macro Gate is active ({self.btc_gate_reason})")
                signal = 'HOLD'

            logger.info(f"[{symbol}] Signal: {signal} | Regime: {regime} | Score: {priority_score:.2f}")

            # Cache candles to SQLite for interactive dashboard
            database.save_market_candles(symbol, TIMEFRAME, df, regime)
            plot_market_graph(symbol, df, TIMEFRAME)

            candle_atr_pct = eval_result.get('atr_pct', 0.0)
            if signal == 'BUY':
                if symbol not in open_positions:
                    buy_signals.append({
                        'symbol': symbol,
                        'current_price': current_price,
                        'source': source,
                        'priority_score': priority_score,
                        'atr_pct': candle_atr_pct
                    })
                else:
                    if candle_atr_pct > 0:
                        pos_data = open_positions[symbol]
                        if pos_data.get('atr_pct', 0.0) == 0.0 or abs(pos_data.get('atr_pct', 0.0) - candle_atr_pct) > 0.0001:
                            pos_data['atr_pct'] = candle_atr_pct
                            database.upsert_position(pos_data)

        # Execute filtered BUY signals
        if buy_signals:
            buy_signals = self.risk_engine.sort_buy_signals(buy_signals)
            for b_sig in buy_signals:
                symbol = b_sig['symbol']
                current_price = b_sig['current_price']
                source = b_sig['source']
                active_count = len(self.tracker.get_open_positions())
                pair_quote = symbol.split('/')[1] if '/' in symbol else self.quote_sample
                priority_score = b_sig.get('priority_score', 0.0)

                is_valid, reason, size_usd = self.risk_engine.validate_entry(
                    symbol, 
                    active_count, 
                    pair_quote, 
                    current_price=current_price,
                    priority_score=priority_score,
                    source=source
                )
                if is_valid:
                    res = self.executor.execute_order(symbol, 'buy', size_usd)
                    if res:
                        fill_price = res.get('price', current_price)
                        fill_amount = res.get('amount', 0.0)
                        quote = res.get('quote', pair_quote)
                        self.tracker.record_entry(
                            symbol, fill_price, fill_amount, quote, 
                            source=source, atr_pct=b_sig.get('atr_pct', 0.0)
                        )
                else:
                    logger.info(f"[{symbol}] BUY skipped: {reason}")

    def run_micro_scalp_cycle(self):
        """
        Dual-Timeframe Micro-Momentum Scalper (3m/5m) cycle.
        """
        if not MICRO_MOMENTUM_ENABLED:
            return

        active_count = len(self.tracker.get_open_positions())
        if active_count >= self.risk_engine.max_positions:
            return

        micro_candidates = list(self.active_universe)
        for m_sym in micro_candidates:
            if m_sym in self.tracker.get_open_positions():
                continue
            try:
                micro_df = self.market_data.fetch_ohlcv(m_sym, MICRO_TIMEFRAME, limit=35)
                if micro_df.empty or len(micro_df) < 25:
                    continue

                m_rev_score = 0.0
                m_rev_tech = 0.0
                if USE_REVOLUT_SIGNALS:
                    m_intel = self.revolut_bridge.get_sentiment(m_sym)
                    if m_intel:
                        m_rev_score = m_intel.get('composite_score', 0.0)
                        m_rev_tech = m_intel.get('tech_score', 0.0)

                if m_rev_tech < -0.20:
                    continue

                micro_eval = self.strategy.check_micro_momentum(
                    m_sym, 
                    micro_df, 
                    rev_score=m_rev_score,
                    min_vol_ratio=MICRO_VOLUME_SURGE_RATIO
                )

                if micro_eval.get('signal') == 'BUY':
                    m_curr_p = float(micro_df['close'].iloc[-1])
                    m_pair_quote = m_sym.split('/')[1] if '/' in m_sym else self.quote_sample
                    m_priority = micro_eval.get('priority_score', 50.0)
                    m_source = "micro_momentum+revolut_ai" if m_rev_score >= 0.20 else "micro_momentum"

                    if not self.btc_safe and "BTC/" not in m_sym:
                        logger.warning(f"[{m_sym}] Micro BUY suppressed by BTC Macro Gate: {self.btc_gate_reason}")
                        continue

                    is_valid, m_reason, m_size_usd = self.risk_engine.validate_entry(
                        m_sym,
                        active_count,
                        m_pair_quote,
                        current_price=m_curr_p,
                        priority_score=m_priority,
                        source=m_source
                    )

                    if is_valid:
                        logger.info(
                            f"⚡ [MICRO-MOMENTUM SCALPER] Volume surge & momentum triggered on {m_sym} ({MICRO_TIMEFRAME})! "
                            f"Order size: ${m_size_usd:.2f} (Priority: {m_priority:.1f})"
                        )
                        res = self.executor.execute_order(m_sym, 'buy', m_size_usd)
                        if res:
                            m_fill_p = res.get('price', m_curr_p)
                            m_fill_amt = res.get('amount', 0.0)
                            m_quote = res.get('quote', m_pair_quote)
                            self.tracker.record_entry(
                                m_sym, m_fill_p, m_fill_amt, m_quote, 
                                source=m_source, atr_pct=micro_eval.get('atr_pct', 0.0)
                            )
                            active_count = len(self.tracker.get_open_positions())
                            if active_count >= self.risk_engine.max_positions:
                                break
            except Exception as e:
                logger.debug(f"[Micro Scalper] Error scanning {m_sym}: {e}")

    def run(self):
        """
        Main execution loop.
        """
        if not self.skip_lock and not self.acquire_instance_lock():
            sys.exit(1)

        self.is_running = True
        logger.info("=" * 60)
        logger.info("Starting Advanced Algorithmic Trading Bot")
        logger.info(f"Execution Mode: {'🟡 DRY RUN (Paper Simulation)' if DRY_RUN else '🟢 LIVE TRADING'}")
        logger.info(f"Dynamic Screener: {'Enabled' if DYNAMIC_SCREENER else 'Disabled'}")
        logger.info(f"Revolut AI Bridge: {'Enabled' if USE_REVOLUT_SIGNALS else 'Disabled'}")
        logger.info(f"Micro Scalper: {'Enabled (' + MICRO_TIMEFRAME + ')' if MICRO_MOMENTUM_ENABLED else 'Disabled'}")
        logger.info(f"Timeframe: {TIMEFRAME} ({self.candle_period}s interval)")
        logger.info("=" * 60)

        # Baseline snapshot
        self.sync_portfolio_state()

        try:
            while self.is_running:
                current_time = time.time()

                # 1. Kill-Switch Check (Standby mode)
                if self.check_emergency_kill_switch():
                    time.sleep(10)
                    continue

                # 2. Position Monitor (every 10s)
                self.monitor_open_positions()

                # 3. Candle Ingestion Scan (runs immediately on startup, then aligned to candle close + 3s)
                current_aligned_cycle = current_time - (current_time % self.candle_period)
                target_scan_time = current_aligned_cycle + 3

                if self.first_scan or (current_time >= target_scan_time and self.last_scan_time < current_aligned_cycle):
                    self.first_scan = False
                    self.last_scan_time = current_time
                    self.run_candle_scan_cycle()

                # 4. Micro-Momentum Scalper (every MICRO_SCAN_INTERVAL seconds)
                if MICRO_MOMENTUM_ENABLED and (current_time - self.last_micro_scan_time >= MICRO_SCAN_INTERVAL):
                    self.last_micro_scan_time = current_time
                    self.run_micro_scalp_cycle()

                time.sleep(10)

        except KeyboardInterrupt:
            logger.info("Termination signal received. Shutting down gracefully.")
        except Exception as e:
            logger.critical(f"Unexpected fatal error in trading engine: {e}", exc_info=True)
            raise
