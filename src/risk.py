import logging
from typing import Tuple
from .portfolio import PortfolioManager
from .database import get_trade_history, get_equity_snapshots, set_kill_switch

logger = logging.getLogger(__name__)

class RiskEngine:
    def __init__(self, portfolio: PortfolioManager):
        self.portfolio = portfolio
        self.max_positions = 2
        self.sizing_ratio = 0.33
        self.min_notional = 5.50
        self.equity_floor = 20.00
        self.max_drawdown_pct = 5.0

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
            peak_equity = max(s['equity'] for s in snapshots)
            if peak_equity > 0:
                drawdown = ((peak_equity - current_equity) / peak_equity) * 100.0
                if drawdown >= self.max_drawdown_pct:
                    reason = f"Max daily drawdown breached: {drawdown:.2f}% >= {self.max_drawdown_pct}%"
                    set_kill_switch(True)
                    return False, reason

        return True, "Safe"

    def calculate_position_size(self, quote: str = 'USDC') -> float:
        """
        Calculates dynamic order size clamped to min notional and available balance.
        Subtracts BNB fee reserve (~$3) from spendable capital per risk rules.
        """
        BNB_RESERVE_USD = 3.0  # Reserved for exchange fee discounts per quantitative-risk-rules.md
        total_equity = self.portfolio.get_total_equity_usd()
        available_quote = self.portfolio.get_available_quote_balance(quote)
        
        # Deduct BNB reserve and a 0.1% fee buffer from spendable capital
        spendable = max(0.0, available_quote - BNB_RESERVE_USD)
        
        # Max 33% of total equity
        target_size = total_equity * self.sizing_ratio
        
        # Clamp to minimum notional
        size = max(target_size, self.min_notional)
        
        # Ensure we stay within spendable balance (buffer 0.1% for fees)
        if size * 1.001 > spendable:
            logger.warning(f"RiskEngine: Desired size {size:.2f} exceeds spendable {spendable:.2f} (after ${BNB_RESERVE_USD} BNB reserve)")
            size = spendable / 1.001  # Leave room for fees
            
        return size

    def validate_entry(self, symbol: str, active_positions_count: int, quote: str = 'USDC') -> Tuple[bool, str, float]:
        """
        Validates if we can enter a new position and returns the allowed size.
        Returns (is_valid, reason, size_usd).
        """
        if active_positions_count >= self.max_positions:
            return False, f"Max concurrent positions reached ({self.max_positions})", 0.0
            
        is_safe, cb_reason = self.check_circuit_breakers()
        if not is_safe:
            return False, f"Circuit Breaker Triggered: {cb_reason}", 0.0
            
        size = self.calculate_position_size(quote)
        if size < self.min_notional:
            return False, f"Available quote insufficient to meet min notional {self.min_notional}", 0.0
            
        return True, "Valid", size
