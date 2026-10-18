import logging
import time
from typing import Tuple, Optional
from datetime import datetime
from .config import (
    CAPITAL_PER_TRADE,
    CONVICTION_SIZING_ENABLED,
    TIER1_A_PLUS_MAX_SIZE,
    TIER2_STANDARD_SIZE,
    TIER3_SPECULATIVE_SIZE,
    SINGLE_SLOT_POWER_SIZE
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
        self.sizing_ratio = 0.25  # Standard 25% equity per position
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

    def calculate_position_size(
        self, 
        active_positions_count: int, 
        quote: str = 'USDC',
        priority_score: float = 0.0,
        source: str = ""
    ) -> float:
        """
        Calculates conviction-weighted order size clamped to min notional, risk limits, and spendable balance.
        Tier 1 (A+ Ultra-Conviction): Score >= 70 or Revolut AI high-confluence -> ~$10.00 - $12.50 (or $13.50 if 0 active positions)
        Tier 2 (Standard Confirmed): Score 20 - 70 -> ~$6.50 - $7.50
        Tier 3 (Speculative / Mean Reversion): Score < 20 -> ~$5.80 - $6.00
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

        # Determine Conviction Tier
        is_tier1 = (priority_score >= 70.0) or ("revolut_ai" in source.lower() and priority_score >= 45.0)
        is_tier3 = (priority_score < 20.0 and "mean_reversion" in source.lower())

        if CONVICTION_SIZING_ENABLED and is_tier1:
            # Single-Slot Power Mode: If 0 positions currently active, allocate larger power size
            if active_positions_count == 0:
                base_target = SINGLE_SLOT_POWER_SIZE
                max_account_risk = total_equity * 0.45  # Up to 45% equity on single-slot power mode
            else:
                base_target = TIER1_A_PLUS_MAX_SIZE
                max_account_risk = total_equity * 0.40  # Up to 40% equity for Tier 1
            tier_name = "Tier 1 (A+ Ultra-Conviction)"
        elif CONVICTION_SIZING_ENABLED and is_tier3:
            base_target = TIER3_SPECULATIVE_SIZE
            max_account_risk = max(self.min_notional, total_equity * 0.20)
            tier_name = "Tier 3 (Speculative / Mean Reversion)"
        else:
            base_target = TIER2_STANDARD_SIZE if TIER2_STANDARD_SIZE else (CAPITAL_PER_TRADE if CAPITAL_PER_TRADE > 0 else 6.50)
            max_account_risk = max(self.min_notional, total_equity * self.sizing_ratio)
            tier_name = "Tier 2 (Standard Confirmed)"

        target_size = min(max(base_target, self.min_notional), max(self.min_notional, max_account_risk))

        # Final effective size cannot exceed available quote in slot (or spendable quote)
        size = min(target_size, allocation_per_slot)

        logger.info(
            f"[BUDGET SIZING] Conviction: {tier_name} | Target: ${target_size:.2f} | Allocated: ${size:.2f} | "
            f"Spendable: ${spendable:.2f} (Score: {priority_score:.1f}, Source: {source})"
        )

        if size < self.min_notional:
            logger.warning(f"RiskEngine: Desired size {size:.2f} is less than {self.min_notional}. Cannot allocate.")

        return size

    def validate_entry(
        self, 
        symbol: str, 
        active_positions_count: int, 
        quote: str = 'USDC', 
        current_price: float = 0.0,
        priority_score: float = 0.0,
        source: str = ""
    ) -> Tuple[bool, str, float]:
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
            
        size = self.calculate_position_size(
            active_positions_count, 
            quote,
            priority_score=priority_score,
            source=source
        )
        if size < self.min_notional:
            # Check if autonomous rebalancing can replenish quote from excess BNB
            if self.portfolio.auto_rebalance_quote(min_quote_required=self.min_notional):
                size = self.calculate_position_size(
                    active_positions_count, 
                    quote,
                    priority_score=priority_score,
                    source=source
                )

        if size < self.min_notional:
            return False, f"Available quote insufficient to meet min notional {self.min_notional}", 0.0
            
        return True, "Valid", size
