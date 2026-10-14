import logging
import time
from typing import Tuple, Optional, Dict, Any
from datetime import datetime
from .config import (
    CAPITAL_PER_TRADE,
    ATR_SL_MULTIPLIER,
    ATR_TP_MR_MULTIPLIER,
    ATR_TP_MOM_MULTIPLIER,
    MAX_LOSS_CAP_PCT
)
from .portfolio import PortfolioManager
from .database import (
    get_trade_history, 
    get_equity_snapshots, 
    set_kill_switch, 
    is_symbol_in_cooldown, 
    get_last_trade
)

logger = logging.getLogger(__name__)

class RiskEngine:
    def __init__(self, portfolio: PortfolioManager):
        self.portfolio = portfolio
        self.max_positions = 2
        self.sizing_ratio = 0.25  # Max 25% equity per position to limit stop-loss downside to ~$0.16
        self.min_notional = 6.00
        self.equity_floor = 20.00
        self.max_drawdown_pct = 5.0

    def sort_buy_signals(self, signals: list) -> list:
        """
        Sorts concurrent BUY signals by priority_score descending.
        """
        return sorted(signals, key=lambda x: x.get('priority_score', 0.0), reverse=True)

    def check_circuit_breakers(self) -> Tuple[bool, str]:
        """
        Validates equity floor and 24h rolling drawdown.
        Returns (is_safe, reason). If not safe, triggers kill-switch.
        """
        current_equity = self.portfolio.get_total_equity_usd()
        
        if current_equity < self.equity_floor:
            reason = f"Equity floor breached: {current_equity:.2f} < {self.equity_floor}"
            set_kill_switch(True)
            return False, reason
            
        snapshots = get_equity_snapshots(hours=24)
        if snapshots:
            # Check 24h rolling baseline
            baseline_equity = snapshots[0]['equity']
            if baseline_equity > 0:
                drawdown = (current_equity - baseline_equity) / baseline_equity
                if drawdown <= -(self.max_drawdown_pct / 100.0):
                    reason = f"Max daily drawdown breached: {drawdown*100:.2f}% <= -{self.max_drawdown_pct}%"
                    set_kill_switch(True)
                    return False, reason

        return True, "Safe"

    def calculate_position_size(self, active_positions_count: int, quote: str = 'USDC') -> float:
        """
        Calculates dynamic order size clamped to min notional, CAPITAL_PER_TRADE, and available balance.
        Subtracts BNB fee reserve (~$3) from spendable capital per risk rules.
        """
        total_equity = self.portfolio.get_total_equity_usd()
        available_quote = self.portfolio.get_available_quote_balance(quote)
        
        # Check if actual BNB is already held to cover fee reserve ($3)
        bnb_reserve_needed = 3.0
        try:
            balances = self.portfolio.fetch_balances()
            if isinstance(balances, dict) and 'BNB' in balances:
                bnb_free = balances.get('BNB', {}).get('free', 0.0)
                if isinstance(bnb_free, (int, float)) and bnb_free >= 0.005:  # ~0.005 BNB is ~$3.00 at $600/BNB
                    bnb_reserve_needed = 0.0
        except Exception:
            pass

        # Deduct needed BNB reserve from spendable quote
        spendable = max(0.0, available_quote - bnb_reserve_needed)
        available_slots = max(1, self.max_positions - active_positions_count)
        allocation_per_slot = spendable / available_slots
        
        # Sizing rules:
        # 1. Base target is CAPITAL_PER_TRADE (e.g. 6.50 USDC)
        # 2. Hard cap per trade: 25% of total account equity (e.g. on $30 equity, max $7.50)
        # 3. Minimum notional: self.min_notional ($6.00)
        configured_size = CAPITAL_PER_TRADE if (CAPITAL_PER_TRADE and CAPITAL_PER_TRADE > 0) else 6.50
        max_account_risk = max(self.min_notional, total_equity * self.sizing_ratio)
        target_size = min(max(configured_size, self.min_notional), max_account_risk)
        
        # Final effective size cannot exceed available quote in slot
        size = min(target_size, allocation_per_slot)
        
        if size < self.min_notional:
            logger.warning(f"RiskEngine: Desired size {size:.2f} is less than {self.min_notional}. Cannot allocate.")
            
        return size

    def validate_entry(self, symbol: str, active_positions_count: int, quote: str = 'USDC', current_price: float = 0.0) -> Tuple[bool, str, float]:
        """
        Validates if we can enter a new position and returns the allowed size.
        Returns (is_valid, reason, size_usd).
        """
        is_cool, cool_reason = is_symbol_in_cooldown(symbol)
        if is_cool:
            return False, cool_reason, 0.0

        # Anti-Chasing / Anti-Re-Entry Guard:
        # Prevent re-entering at a higher price than the recent exit within 4 hours (e.g., chasing top of wicks)
        if current_price > 0.0:
            last_trade = get_last_trade(symbol)
            if last_trade:
                last_exit_time_str = last_trade.get('exit_time')
                last_exit_price = last_trade.get('exit_price', 0.0)
                if last_exit_time_str and last_exit_price > 0.0:
                    try:
                        exit_dt = datetime.strptime(last_exit_time_str, "%Y-%m-%d %H:%M:%S")
                        elapsed_sec = (datetime.now() - exit_dt).total_seconds()
                    except Exception:
                        elapsed_sec = 0.0
                    
                    if 0 <= elapsed_sec < 14400.0:  # Within 4 hours of exit
                        if current_price >= last_exit_price:
                            return False, f"Anti-chasing guard: Current price ({current_price:.4f}) >= last exit ({last_exit_price:.4f}) within 4h. Wait for pullback.", 0.0

        if active_positions_count >= self.max_positions:
            return False, f"Max concurrent positions reached ({self.max_positions})", 0.0
            
        is_safe, cb_reason = self.check_circuit_breakers()
        if not is_safe:
            return False, f"Circuit Breaker Triggered: {cb_reason}", 0.0
            
        size = self.calculate_position_size(active_positions_count, quote)
        if size < self.min_notional:
            # Check if autonomous rebalancing can replenish quote from excess BNB
            if self.portfolio.auto_rebalance_quote(min_quote_required=self.min_notional):
                size = self.calculate_position_size(active_positions_count, quote)

        if size < self.min_notional:
            return False, f"Available quote insufficient to meet min notional {self.min_notional}", 0.0
            
        return True, "Valid", size

    def calculate_dynamic_targets(self, entry_price: float, atr: float, strategy_source: str = 'breakout') -> Dict[str, float]:
        """
        Calculates ATR-dynamic Stop-Loss and Take-Profit targets.
        - Mean Reversion: SL = max(entry - 1.5 * ATR, entry * 0.975), TP = entry + 2.0 * ATR
        - Momentum Breakout: SL = max(entry - 1.5 * ATR, entry * 0.975), TP = entry + 3.5 * ATR, Trail = 1.5 * ATR
        """
        is_trend_or_swing = any(k in strategy_source.lower() for k in ["breakout", "revolut", "swing"])
        sl_cap = entry_price * (1.0 - MAX_LOSS_CAP_PCT / 100.0)
        
        if atr > 0:
            sl_price = max(entry_price - ATR_SL_MULTIPLIER * atr, sl_cap)
            tp_mult = ATR_TP_MOM_MULTIPLIER if is_trend_or_swing else ATR_TP_MR_MULTIPLIER
            tp_price = entry_price + tp_mult * atr
            trail_distance = ATR_SL_MULTIPLIER * atr
        else:
            sl_price = sl_cap
            tp_price = entry_price * (1.05 if is_trend_or_swing else 1.025)
            trail_distance = entry_price * 0.010

        return {
            "stop_loss_price": sl_price,
            "take_profit_price": tp_price,
            "trail_distance": trail_distance,
            "atr": atr
        }

