import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List

from .config import (
    TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_DISTANCE,
    HARD_STOP_LOSS,
    TAKE_PROFIT_TARGET
)
from . import database
from .notifications import send_webhook_alert

logger = logging.getLogger(__name__)

class PositionTracker:
    def __init__(self, pos_file: Optional[str] = None, hist_file: Optional[str] = None):
        # Database is automatically initialized when imported
        self.pos_file = pos_file
        self.hist_file = hist_file

    def get_open_positions(self) -> Dict[str, Any]:
        return database.get_open_positions()

    def get_open_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        return database.get_open_position(symbol)

    def record_entry(self, symbol: str, entry_price: float, amount: float, quote: str = "USDC", source: str = "unknown") -> Dict[str, Any]:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        pos_data = {
            "symbol": symbol,
            "entry_price": float(entry_price),
            "peak_price": float(entry_price),
            "amount": float(amount),
            "quote": quote,
            "trailing_active": False,
            "entry_time": now_str,
            "source": source
        }
        
        database.upsert_position(pos_data)
        logger.info(f"[POSITION OPENED] {symbol} @ {entry_price:.4f} | Amount: {amount} {symbol.split('/')[0]}")
        
        # Webhook Notification
        msg = f"**Symbol:** {symbol}\n**Entry Price:** {entry_price:.4f} {quote}\n**Amount:** {amount}"
        send_webhook_alert(f"Position Opened: {symbol}", msg, color=65280) # Green
            
        return pos_data

    def check_position_exit(self, symbol: str, current_price: float) -> Optional[Tuple[str, str, float]]:
        """
        Evaluates an open position against Stop-Loss, Trailing Stop, and Take-Profit rules based on source strategy.
        Returns: (exit_type, reason, pnl_pct) or None if holding.
        """
        pos = database.get_open_position(symbol)
        if not pos:
            return None

        entry_price = pos["entry_price"]
        peak_price = pos.get("peak_price", entry_price)
        trailing_active = pos.get("trailing_active", False)
        source = pos.get("source", "unknown")

        changed = False
        # 1. Update highest price if reached new peak
        if current_price > peak_price:
            peak_price = current_price
            pos["peak_price"] = peak_price
            changed = True

        # 2. Calculate PnL percentage relative to entry
        gain_pct = ((current_price - entry_price) / entry_price) * 100.0
        peak_gain_pct = ((peak_price - entry_price) / entry_price) * 100.0

        # 3. Check Trailing Stop arming / activation
        is_micro_scalp = any(k in source.lower() for k in ["micro", "scalp"])
        is_trend_or_swing = any(k in source.lower() for k in ["breakout", "revolut", "swing", "pullback", "ai", "trend"]) and not is_micro_scalp

        if is_micro_scalp:
            activation_threshold = 1.2  # Fast scalp activation at +1.2%
        else:
            activation_threshold = TRAILING_STOP_ACTIVATION if TRAILING_STOP_ACTIVATION else 2.0

        if peak_gain_pct >= activation_threshold and not trailing_active:
            trailing_active = True
            pos["trailing_active"] = True
            changed = True
            logger.info(f"[{symbol}] Trailing Stop ARMED! Peak Gain: +{peak_gain_pct:.2f}% (Threshold: +{activation_threshold}%)")

        if changed:
            database.upsert_position(pos)

        # 4. Handle Stop-Loss, Trailing Stop, and Take-Profit logic per strategy
        trail_dist_ratio = (TRAILING_STOP_DISTANCE / 100.0) if TRAILING_STOP_DISTANCE else 0.015
        if is_micro_scalp:
            micro_trail = 0.006  # Tight 0.6% trail for fast scalps
            micro_breakeven_floor = entry_price * 1.003  # +0.3% floor
            trailing_trigger_price = max(peak_price * (1.0 - micro_trail), micro_breakeven_floor)
        elif peak_gain_pct >= 3.0:
            runner_floor = entry_price * 1.015  # Guaranteed profit floor of +1.5%
            trailing_trigger_price = max(peak_price * (1.0 - trail_dist_ratio), runner_floor)
        else:
            breakeven_floor = entry_price * 1.002  # +0.2% ensures fees and small profit are locked in
            trailing_trigger_price = max(peak_price * (1.0 - trail_dist_ratio), breakeven_floor)

        if is_micro_scalp:
            # Fast Micro-Scalp Exits
            if trailing_active and current_price <= trailing_trigger_price:
                reason = f"Micro Scalp Trailing Stop hit: Price fell to {current_price:.4f} from peak {peak_price:.4f} (+{gain_pct:.2f}%)"
                return ("TRAILING_STOP", reason, gain_pct)

            if gain_pct <= -1.0:
                reason = f"Micro Scalp Hard Stop-Loss hit: Price dropped {gain_pct:.2f}% (Limit: -1.0%)"
                return ("HARD_STOP_LOSS", reason, gain_pct)

            if gain_pct >= 2.5:
                reason = f"Micro Scalp Take-Profit reached: +{gain_pct:.2f}% (Target: +2.5%)"
                return ("TAKE_PROFIT", reason, gain_pct)

        elif is_trend_or_swing:
            # Trailing stop fires only if armed and price fell by trail_distance from peak
            if trailing_active and current_price <= trailing_trigger_price:
                reason = f"Trailing Stop hit: Price fell to {current_price:.4f} from peak {peak_price:.4f} (+{gain_pct:.2f}%)"
                return ("TRAILING_STOP", reason, gain_pct)
            
            sl_limit = -abs(HARD_STOP_LOSS) if HARD_STOP_LOSS else -2.5
            if gain_pct <= sl_limit:
                reason = f"Hard Stop-Loss hit: Price dropped {gain_pct:.2f}% (Limit: {sl_limit:.1f}%)"
                return ("HARD_STOP_LOSS", reason, gain_pct)

            tp_target = TAKE_PROFIT_TARGET if TAKE_PROFIT_TARGET else 5.0
            if gain_pct >= tp_target:
                reason = f"Take-Profit hit: +{gain_pct:.2f}% (Target: +{tp_target:.1f}%)"
                return ("TAKE_PROFIT", reason, gain_pct)

        else:
            # Mean Reversion: quick targets
            if trailing_active and current_price <= trailing_trigger_price:
                reason = f"MR Trailing Stop hit: Price fell to {current_price:.4f} from peak {peak_price:.4f} (+{gain_pct:.2f}%)"
                return ("TRAILING_STOP", reason, gain_pct)

            if gain_pct <= -1.0:
                reason = f"MR Hard Stop-Loss hit: Price dropped {gain_pct:.2f}% (Limit: -1.0%)"
                return ("HARD_STOP_LOSS", reason, gain_pct)
            
            if gain_pct >= 2.5:
                reason = f"MR Take-Profit Target reached: +{gain_pct:.2f}% (Target: +2.5%)"
                return ("TAKE_PROFIT", reason, gain_pct)

        # 5. Stagnation Timeout: Exit momentum-less zombie trades held without momentum
        entry_time_str = pos.get("entry_time")
        if entry_time_str:
            try:
                if "T" in entry_time_str:
                    entry_dt = datetime.fromisoformat(entry_time_str)
                else:
                    entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
                elapsed_sec = (datetime.now() - entry_dt).total_seconds()
                timeout_threshold = 2700.0 if is_micro_scalp else 14400.0  # 45m for micro scalps, 4h for swings
                if elapsed_sec >= timeout_threshold and not trailing_active and gain_pct <= 0.4:
                    elapsed_desc = f"{elapsed_sec/60:.0f}m" if is_micro_scalp else f"{elapsed_sec/3600.0:.1f}h"
                    reason = f"Stagnation timeout: held {elapsed_desc} without momentum ({gain_pct:+.2f}%)"
                    return ("STAGNATION_TIMEOUT", reason, gain_pct)
            except Exception as e:
                logger.debug(f"[{symbol}] Could not parse entry_time '{entry_time_str}': {e}")

        return None

    def record_exit(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict[str, Any]]:
        pos = database.get_open_position(symbol)
        if not pos:
            return None

        entry_price = pos["entry_price"]
        amount = pos["amount"]
        quote = pos.get("quote", "USDC")
        
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0
        realized_pnl = (exit_price - entry_price) * amount
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        trade_record = {
            "symbol": symbol,
            "entry_time": pos["entry_time"],
            "exit_time": now_str,
            "entry_price": entry_price,
            "exit_price": float(exit_price),
            "amount": amount,
            "quote": quote,
            "pnl_pct": round(pnl_pct, 2),
            "realized_pnl": round(realized_pnl, 4),
            "reason": reason
        }

        # Update database
        database.remove_position(symbol)
        database.insert_trade_history(trade_record)

        # Differentiated Cooldown Lifecycle:
        # Stagnant / Timed-out trades: 1h cooldown (3600s) to allow market to reset without long lockouts
        # Winning / Profitable trades: 90m cooldown (5400s) to allow market consolidation after profit taking
        # Losing / Stopped-out trades: 4h cooldown (14400s) to prevent revenge trading or knife-catching
        is_stagnation = "stagnation" in reason.lower()
        is_profitable = (realized_pnl > 0) or (pnl_pct > 0)
        is_micro = "micro" in reason.lower() or "scalp" in reason.lower()
        if is_micro:
            cooldown_duration = 600.0 if is_profitable else 900.0  # 10m on profit, 15m on loss/timeout
            cooldown_reason = f"Micro Scalp Cooldown ({int(cooldown_duration/60)}m): {reason}"
        elif is_stagnation:
            cooldown_duration = 3600.0  # 1 hour
            cooldown_reason = f"Stagnation Cooldown (1h): {reason}"
        elif is_profitable:
            cooldown_duration = 5400.0  # 90 minutes
            cooldown_reason = f"Profit Cooldown (90m): {reason}"
        else:
            cooldown_duration = 14400.0  # 4 hours
            cooldown_reason = f"Loss Cooldown (4h): {reason}"

        database.set_symbol_cooldown(symbol, duration_seconds=cooldown_duration, reason=cooldown_reason)

        logger.info(f"[POSITION CLOSED] {symbol} @ {exit_price:.4f} | Reason: {reason} | PnL: {pnl_pct:+.2f}% ({realized_pnl:+.4f} {quote})")
        
        # Webhook Notification
        color = 65280 if realized_pnl > 0 else 16711680 # Green if profit, Red if loss
        msg = f"**Symbol:** {symbol}\n**Exit Price:** {exit_price:.4f} {quote}\n**PnL:** {pnl_pct:+.2f}% ({realized_pnl:+.4f} {quote})\n**Reason:** {reason}"
        send_webhook_alert(f"Position Closed: {symbol}", msg, color=color)
        
        return trade_record

    def get_trade_history(self) -> List[Dict[str, Any]]:
        return database.get_trade_history()

