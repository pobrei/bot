import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from .strategy import Strategy
from .config import MIN_ATR_RATIO, TIMEFRAME, get_default_min_atr_ratio

logger = logging.getLogger(__name__)

def norm_cdf(z: float) -> float:
    """Standard Normal Cumulative Distribution Function using Python math.erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

def calculate_psr(returns: pd.Series, benchmark_sr: float = 0.0, periods_per_year: float = 35040.0) -> float:
    """
    Calculates the Probabilistic Sharpe Ratio (PSR) based on Bailey & López de Prado (2012).
    Corrects the observed Sharpe ratio for non-normality (skewness and kurtosis) and sample length.
    
    PSR = Phi( ((SR - SR*) * sqrt(N - 1)) / sqrt(1 - skew * SR + ((kurt - 1) / 4) * SR^2) )
    """
    if len(returns) < 5:
        return 0.5
        
    mean_ret = returns.mean()
    std_ret = returns.std(ddof=1)
    if std_ret == 0 or pd.isna(std_ret):
        return 0.5

    n = len(returns)
    sr = (mean_ret / std_ret) * math.sqrt(periods_per_year)
    
    skew = float(returns.skew()) if not pd.isna(returns.skew()) else 0.0
    # pandas kurt() is excess kurtosis (Fisher, normal = 0); convert to Pearson (normal = 3)
    kurt = float(returns.kurt()) + 3.0 if not pd.isna(returns.kurt()) else 3.0

    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)
    if denom_sq <= 0:
        return 1.0 if sr > benchmark_sr else 0.0

    z_stat = ((sr - benchmark_sr) * math.sqrt(n - 1.0)) / math.sqrt(denom_sq)
    return norm_cdf(z_stat)

class EventDrivenPaperEngine:
    """
    Event-driven historical simulation engine enforcing parity with live execution:
    - Bar-by-bar chronological processing (no lookahead bias)
    - Dynamic Dual-Regime Strategy (ADX filter, MR Engine A, Breakout Engine B)
    - Realistic execution friction (Binance 0.075% BNB-discounted commission, 0.05% slippage, 0.05% spread)
    - Non-bypassable risk controls (concurrency <= 2, dynamic sizing, differentiated cooldown, 5% circuit breaker)
    - Walk-Forward Analysis (70% In-Sample / 30% Out-of-Sample)
    - Probabilistic Sharpe Ratio (PSR) computation
    """
    def __init__(
        self,
        starting_capital: float = 30.0,
        max_positions: int = 2,
        fee_rate: float = 0.00075,      # 0.075% Binance commission (with BNB discount)
        slippage_rate: float = 0.0005,  # 0.05% simulated slippage penalty
        spread_rate: float = 0.0005,    # 0.05% half-spread penalty
        timeframe: str = "15m",
        min_atr_ratio: Optional[float] = None
    ):
        self.starting_capital = starting_capital
        self.max_positions = max_positions
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.spread_rate = spread_rate
        self.timeframe = timeframe
        self.min_atr_ratio = min_atr_ratio if min_atr_ratio is not None else get_default_min_atr_ratio(timeframe)
        self.strategy = Strategy()

    def run_simulation(
        self, 
        df: pd.DataFrame, 
        symbol: str = "BTC/USDC",
        walk_forward_split: float = 0.70
    ) -> Dict[str, Any]:
        """
        Runs the event-driven paper trading verification on the provided OHLCV DataFrame.
        """
        if df.empty or len(df) < 50:
            raise ValueError("Insufficient historical bars for event-driven simulation (minimum 50 required).")

        df = df.copy()
        if 'timestamp' in df.columns:
            if pd.api.types.is_numeric_dtype(df['timestamp']):
                df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            else:
                df['datetime'] = pd.to_datetime(df['timestamp'])
        else:
            df['datetime'] = df.index

        total_bars = len(df)
        split_idx = int(total_bars * walk_forward_split)

        # State tracking
        equity = self.starting_capital
        available_cash = self.starting_capital
        open_positions: Dict[str, Dict[str, Any]] = {}
        trade_log: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        hourly_equity_history: List[Tuple[pd.Timestamp, float]] = []

        cooldown_expiry: Optional[pd.Timestamp] = None

        # Circuit breaker state
        cb_triggered_until: Optional[pd.Timestamp] = None
        min_equity_floor = 20.00

        # Bar-by-bar chronological execution
        for i in range(30, total_bars):
            curr_bar = df.iloc[i]
            bar_time = curr_bar['datetime']
            bar_close = float(curr_bar['close'])
            bar_high = float(curr_bar['high'])
            bar_low = float(curr_bar['low'])

            is_out_of_sample = (i >= split_idx)
            data_segment = "Out-of-Sample" if is_out_of_sample else "In-Sample"

            # 1. Update 24h rolling equity snapshots for circuit breaker
            hourly_equity_history.append((bar_time, equity))
            # Prune snapshots older than 24h
            cutoff_24h = bar_time - timedelta(hours=24)
            hourly_equity_history = [(t, eq) for (t, eq) in hourly_equity_history if t >= cutoff_24h]

            # 2. Check Circuit Breaker (5% rolling drawdown or equity floor $20)
            if hourly_equity_history:
                max_24h_equity = max(eq for (_, eq) in hourly_equity_history)
                drawdown_24h = (equity - max_24h_equity) / max_24h_equity if max_24h_equity > 0 else 0.0
                if drawdown_24h <= -0.05 or equity < min_equity_floor:
                    if cb_triggered_until is None or bar_time >= cb_triggered_until:
                        cb_triggered_until = bar_time + timedelta(hours=24)
                        # Liquidate open position under circuit breaker
                        if symbol in open_positions:
                            pos = open_positions.pop(symbol)
                            exit_price = bar_close * (1.0 - self.slippage_rate - self.spread_rate)
                            pnl_pct = ((exit_price - pos['entry_price']) / pos['entry_price']) * 100.0
                            gross_proceeds = pos['amount'] * exit_price
                            exit_fee = gross_proceeds * self.fee_rate
                            net_proceeds = gross_proceeds - exit_fee
                            realized_pnl = net_proceeds - pos['cost_basis']
                            equity += realized_pnl
                            available_cash += net_proceeds
                            trade_log.append({
                                'symbol': symbol,
                                'entry_time': pos['entry_time'],
                                'exit_time': bar_time,
                                'entry_price': pos['entry_price'],
                                'exit_price': exit_price,
                                'amount': pos['amount'],
                                'pnl_pct': pnl_pct,
                                'realized_pnl': realized_pnl,
                                'reason': 'CIRCUIT_BREAKER_TRIGGERED',
                                'segment': data_segment,
                                'source': pos['source']
                            })
                            is_profitable = (realized_pnl > 0) or (pnl_pct > 0)
                            cd_dur = timedelta(minutes=45) if is_profitable else timedelta(hours=4)
                            cooldown_expiry = bar_time + cd_dur

            # 3. Manage Open Position (Trailing Stop, Hard Stop-Loss, Take-Profit)
            if symbol in open_positions:
                pos = open_positions[symbol]
                entry_price = pos['entry_price']
                peak_price = pos['peak_price']
                source = pos['source']
                trailing_active = pos['trailing_active']

                # Update peak price using bar high
                if bar_high > peak_price:
                    peak_price = bar_high
                    pos['peak_price'] = peak_price

                peak_gain_pct = ((peak_price - entry_price) / entry_price) * 100.0
                curr_gain_pct = ((bar_close - entry_price) / entry_price) * 100.0

                # Check trailing stop arming (+1.5%)
                if peak_gain_pct >= 1.5 and not trailing_active:
                    pos['trailing_active'] = True
                    trailing_active = True

                exit_triggered = False
                exit_reason = ""
                exit_price = bar_close

                # Regime-specific rules
                is_trend = any(k in source.lower() for k in ["breakout", "trend"])
                if is_trend:
                    trail_trigger = peak_price * (1.0 - 0.012) # 1.2% trailing stop
                    if trailing_active and bar_low <= trail_trigger:
                        exit_triggered = True
                        exit_price = min(bar_close, trail_trigger)
                        exit_reason = f"TRAILING_STOP: Trailing Stop Hit (-1.2% from peak {peak_price:.4f})"
                    elif curr_gain_pct <= -1.5 or bar_low <= entry_price * 0.985:
                        exit_triggered = True
                        exit_price = entry_price * 0.985
                        exit_reason = "HARD_STOP_LOSS: Breakout Hard Stop (-1.5%)"
                    elif curr_gain_pct >= 5.0 or bar_high >= entry_price * 1.05:
                        exit_triggered = True
                        exit_price = entry_price * 1.05
                        exit_reason = "TAKE_PROFIT: Runner Target Hit (+5.0%)"
                else:
                    # Mean Reversion rules (Section 2)
                    trail_trigger = peak_price * (1.0 - 0.010)
                    if trailing_active and bar_low <= trail_trigger:
                        exit_triggered = True
                        exit_price = min(bar_close, trail_trigger)
                        exit_reason = "TRAILING_STOP: MR Trailing Stop Hit"
                    elif curr_gain_pct <= -1.0 or bar_low <= entry_price * 0.99:
                        exit_triggered = True
                        exit_price = entry_price * 0.99
                        exit_reason = "HARD_STOP_LOSS: MR Hard Stop-Loss (-1.0%)"
                    elif curr_gain_pct >= 2.5 or bar_high >= entry_price * 1.025:
                        exit_triggered = True
                        exit_price = entry_price * 1.025
                        exit_reason = "TAKE_PROFIT: MR Target Hit (+2.5%)"
                    else:
                        # Check technical Mean Reversion flip (Close >= Middle BB or RSI >= 65)
                        df_slice_mr = df.iloc[:i+1].copy()
                        eval_mr = self.strategy.evaluate(symbol, df_slice_mr, min_atr_ratio=self.min_atr_ratio)
                        if eval_mr.get('signal') == 'SELL':
                            exit_triggered = True
                            exit_price = bar_close
                            exit_reason = "TAKE_PROFIT: MR Mean Reversion (Middle BB or RSI >= 65)"

                if exit_triggered:
                    # Apply exit slippage and friction
                    effective_exit = exit_price * (1.0 - self.slippage_rate - self.spread_rate)
                    gross_proceeds = pos['amount'] * effective_exit
                    exit_fee = gross_proceeds * self.fee_rate
                    net_proceeds = gross_proceeds - exit_fee
                    realized_pnl = net_proceeds - pos['cost_basis']
                    pnl_pct = ((effective_exit - entry_price) / entry_price) * 100.0

                    equity += realized_pnl
                    available_cash += net_proceeds
                    del open_positions[symbol]
                    
                    # Differentiated cooldown lifecycle:
                    # 45 min on profit to catch continuations, 4 hours on loss to prevent revenge trading
                    is_profitable = (realized_pnl > 0) or (pnl_pct > 0)
                    cd_dur = timedelta(minutes=45) if is_profitable else timedelta(hours=4)
                    cooldown_expiry = bar_time + cd_dur

                    trade_log.append({
                        'symbol': symbol,
                        'entry_time': pos['entry_time'],
                        'exit_time': bar_time,
                        'entry_price': entry_price,
                        'exit_price': effective_exit,
                        'amount': pos['amount'],
                        'pnl_pct': pnl_pct,
                        'realized_pnl': realized_pnl,
                        'reason': exit_reason,
                        'segment': data_segment,
                        'source': source
                    })

            # 4. Evaluate Entry Signal (Only if no position held, cooldown expired, and CB inactive)
            in_cooldown = (cooldown_expiry is not None) and (bar_time < cooldown_expiry)
            cb_active = (cb_triggered_until is not None) and (bar_time < cb_triggered_until)

            if symbol not in open_positions and not in_cooldown and not cb_active:
                # Slice historical data up to bar i (strict causality)
                df_slice = df.iloc[:i+1].copy()
                eval_res = self.strategy.evaluate(symbol, df_slice, min_atr_ratio=self.min_atr_ratio)
                signal = eval_res.get('signal', 'HOLD')
                source = eval_res.get('source', 'unknown')

                if signal == 'BUY':
                    # Dynamic position sizing
                    # min(max(total_equity * 0.40, 6.00), (available_cash - fee_reserve) / 2)
                    bnb_reserve = 3.00
                    spendable = max(0.0, available_cash - bnb_reserve)
                    size_usd = min(max(equity * 0.40, 6.00), spendable / 2.0)

                    if size_usd >= 6.00 and available_cash >= size_usd:
                        # Apply entry friction (slippage + spread)
                        fill_price = bar_close * (1.0 + self.slippage_rate + self.spread_rate)
                        amount = size_usd / fill_price
                        entry_fee = size_usd * self.fee_rate
                        cost_basis = size_usd + entry_fee

                        available_cash -= cost_basis
                        open_positions[symbol] = {
                            'entry_price': fill_price,
                            'peak_price': fill_price,
                            'amount': amount,
                            'cost_basis': cost_basis,
                            'source': source,
                            'entry_time': bar_time,
                            'trailing_active': False
                        }

            # 5. Record Bar-by-bar Equity Snapshot
            unrealized_pnl = 0.0
            if symbol in open_positions:
                pos = open_positions[symbol]
                cur_val = pos['amount'] * bar_close
                unrealized_pnl = cur_val - pos['cost_basis']

            current_total_equity = available_cash + (pos['amount'] * bar_close if symbol in open_positions else 0.0)
            equity_curve.append({
                'datetime': bar_time,
                'equity': current_total_equity,
                'segment': data_segment
            })

        # Close any remaining position at end of simulation
        if symbol in open_positions:
            pos = open_positions.pop(symbol)
            final_bar = df.iloc[-1]
            exit_price = float(final_bar['close']) * (1.0 - self.slippage_rate - self.spread_rate)
            gross_proceeds = pos['amount'] * exit_price
            exit_fee = gross_proceeds * self.fee_rate
            net_proceeds = gross_proceeds - exit_fee
            realized_pnl = net_proceeds - pos['cost_basis']
            pnl_pct = ((exit_price - pos['entry_price']) / pos['entry_price']) * 100.0
            equity += realized_pnl
            trade_log.append({
                'symbol': symbol,
                'entry_time': pos['entry_time'],
                'exit_time': final_bar['datetime'],
                'entry_price': pos['entry_price'],
                'exit_price': exit_price,
                'amount': pos['amount'],
                'pnl_pct': pnl_pct,
                'realized_pnl': realized_pnl,
                'reason': 'END_OF_SIMULATION_CLOSE',
                'segment': 'Out-of-Sample',
                'source': pos['source']
            })

        # Calculate Full Performance Metrics
        df_eq = pd.DataFrame(equity_curve)
        df_eq['return'] = df_eq['equity'].pct_change().fillna(0.0)

        summary_metrics = self._compute_metrics(trade_log, df_eq, self.starting_capital)

        # In-Sample vs Out-of-Sample Split Metrics
        is_trades = [t for t in trade_log if t['segment'] == 'In-Sample']
        oos_trades = [t for t in trade_log if t['segment'] == 'Out-of-Sample']

        is_eq = df_eq[df_eq['segment'] == 'In-Sample'].copy()
        oos_eq = df_eq[df_eq['segment'] == 'Out-of-Sample'].copy()

        is_metrics = self._compute_metrics(is_trades, is_eq, self.starting_capital)
        oos_starting_cap = is_eq['equity'].iloc[-1] if not is_eq.empty else self.starting_capital
        oos_metrics = self._compute_metrics(oos_trades, oos_eq, oos_starting_cap)

        return {
            'symbol': symbol,
            'timeframe': self.timeframe,
            'total_bars': total_bars,
            'split_idx': split_idx,
            'summary': summary_metrics,
            'in_sample': is_metrics,
            'out_of_sample': oos_metrics,
            'trade_log': trade_log,
            'equity_curve': equity_curve
        }

    def _compute_metrics(self, trades: List[Dict[str, Any]], df_eq: pd.DataFrame, start_cap: float) -> Dict[str, Any]:
        """Calculates quantitative validation metrics including PSR, Profit Factor, and Max DD."""
        total_trades = len(trades)
        if total_trades == 0:
            return {
                'total_trades': 0,
                'win_rate_pct': 0.0,
                'profit_factor': 0.0,
                'net_pnl': 0.0,
                'return_pct': 0.0,
                'max_drawdown_pct': 0.0,
                'sharpe_ratio': 0.0,
                'psr': 0.5,
                'win_trades': 0,
                'loss_trades': 0
            }

        pnl_series = pd.Series([t['realized_pnl'] for t in trades])
        wins = pnl_series[pnl_series > 0]
        losses = pnl_series[pnl_series <= 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0

        gross_profit = wins.sum() if not wins.empty else 0.0
        gross_loss = abs(losses.sum()) if not losses.empty else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        net_pnl = pnl_series.sum()
        end_cap = start_cap + net_pnl
        return_pct = (net_pnl / start_cap) * 100.0 if start_cap > 0 else 0.0

        # Drawdown computation
        if not df_eq.empty and 'equity' in df_eq.columns:
            cum_max = df_eq['equity'].cummax()
            dd = (df_eq['equity'] - cum_max) / cum_max
            max_dd = abs(dd.min()) * 100.0
            
            # Annualized Sharpe & PSR (15m candles: 4 per hour * 24 * 365 = 35040 periods/yr)
            periods_per_year = 35040.0 if '15m' in self.timeframe else 8760.0
            returns = df_eq['return']
            mean_r = returns.mean()
            std_r = returns.std(ddof=1)
            sharpe = (mean_r / std_r) * math.sqrt(periods_per_year) if std_r > 0 else 0.0
            psr = calculate_psr(returns, benchmark_sr=0.0, periods_per_year=periods_per_year)
        else:
            max_dd = 0.0
            sharpe = 0.0
            psr = 0.5

        return {
            'total_trades': total_trades,
            'win_rate_pct': round(win_rate, 1),
            'profit_factor': round(profit_factor, 2),
            'net_pnl': round(net_pnl, 4),
            'return_pct': round(return_pct, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'sharpe_ratio': round(sharpe, 2),
            'psr': round(psr, 4),
            'win_trades': win_count,
            'loss_trades': loss_count,
            'avg_win': round(wins.mean(), 4) if not wins.empty else 0.0,
            'avg_loss': round(losses.mean(), 4) if not losses.empty else 0.0
        }
