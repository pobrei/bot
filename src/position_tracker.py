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

    def record_entry(
        self, 
        symbol: str, 
        entry_price: float, 
        amount: float, 
        quote: str = "USDC", 
        source: str = "unknown",
        atr: float = 0.0,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0
    ) -> Dict[str, Any]:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry_price = float(entry_price)
        amount = float(amount)
        atr = float(atr)
        
        # Calculate dynamic targets if not explicitly supplied
        is_trend_or_swing = any(k in source.lower() for k in ["breakout", "revolut", "swing"])
        if stop_loss_price <= 0.0:
            if atr > 0.0:
                stop_loss_price = max(entry_price - 1.5 * atr, entry_price * 0.975)
            else:
                stop_loss_price = entry_price * (0.975 if is_trend_or_swing else 0.990)

        if take_profit_price <= 0.0:
            if atr > 0.0:
                take_profit_price = entry_price + (3.5 * atr if is_trend_or_swing else 2.0 * atr)
            else:
                take_profit_price = entry_price * (1.05 if is_trend_or_swing else 1.025)

        pos_data = {
            "symbol": symbol,
            "entry_price": entry_price,
            "peak_price": entry_price,
            "amount": amount,
            "quote": quote,
            "trailing_active": False,
            "entry_time": now_str,
            "source": source,
            "atr": atr,
            "stop_loss_price": float(stop_loss_price),
            "take_profit_price": float(take_profit_price),
            "ratcheted": False
        }
        
        database.upsert_position(pos_data)
        logger.info(
            f"[POSITION OPENED] {symbol} @ {entry_price:.4f} | SL: {stop_loss_price:.4f} | "
            f"TP: {take_profit_price:.4f} | Amount: {amount} {symbol.split('/')[0]}"
        )
        
        # Webhook Notification
        msg = (
            f"**Symbol:** {symbol}\n**Entry Price:** {entry_price:.4f} {quote}\n"
            f"**Amount:** {amount}\n**Stop-Loss:** {stop_loss_price:.4f}\n**Take-Profit:** {take_profit_price:.4f}"
        )
        send_webhook_alert(f"Position Opened: {symbol}", msg, color=65280) # Green
            
        return pos_data

    def check_position_exit(self, symbol: str, current_price: float) -> Optional[Tuple[str, str, float]]:
        """
        Evaluates an open position against Stop-Loss, Trailing Stop, Breakeven Ratchet, and Take-Profit rules.
        Returns: (exit_type, reason, pnl_pct) or None if holding.
        """
        pos = database.get_open_position(symbol)
        if not pos:
            return None

        entry_price = float(pos["entry_price"])
        peak_price = float(pos.get("peak_price", entry_price))
        trailing_active = bool(pos.get("trailing_active", False))
        source = pos.get("source", "unknown")
        atr = float(pos.get("atr", 0.0))
        sl_price = float(pos.get("stop_loss_price", 0.0))
        tp_price = float(pos.get("take_profit_price", 0.0))
        ratcheted = bool(pos.get("ratcheted", False))

        changed = False
        # 1. Update highest price if reached new peak
        if current_price > peak_price:
            peak_price = current_price
            pos["peak_price"] = peak_price
            changed = True

        # 2. Calculate PnL percentage relative to entry
        gain_pct = ((current_price - entry_price) / entry_price) * 100.0
        peak_gain_pct = ((peak_price - entry_price) / entry_price) * 100.0

        # 3. Breakeven Ratchet (Fee Lock):
        # Once unrealized gain hits +1.2%, ratchet stop_loss_price to entry * 1.003 (+0.3%)
        if gain_pct >= 1.2 and not ratcheted:
            be_stop = entry_price * 1.003
            if be_stop > sl_price:
                sl_price = be_stop
                pos["stop_loss_price"] = be_stop
                pos["ratcheted"] = True
                ratcheted = True
                changed = True
                logger.info(f"[{symbol}] 🛡️ Breakeven Ratchet Activated! Stop raised to {be_stop:.4f} (+0.3% fee lock)")

        # 4. Trailing Stop for Momentum Breakout & Trend Confluence
        is_trend_or_swing = any(k in source.lower() for k in ["breakout", "revolut", "swing"])
        trailing_trigger_price = 0.0
        if is_trend_or_swing:
            # Trailing stop arms at +1.5% peak gain
            if peak_gain_pct >= 1.5 and not trailing_active:
                trailing_active = True
                pos["trailing_active"] = True
                changed = True
                logger.info(f"[{symbol}] Trailing Stop ARMED! Peak Gain: +{peak_gain_pct:.2f}%")

            if trailing_active:
                if peak_gain_pct >= 2.5:
                    trail_dist = 0.006 * peak_price  # Tight 0.6% trail for runners above +2.5%
                elif atr > 0:
                    trail_dist = 1.5 * atr
                else:
                    trail_dist = (TRAILING_STOP_DISTANCE / 100.0) * peak_price if TRAILING_STOP_DISTANCE else (0.010 * peak_price)
                
                trailing_trigger_price = peak_price - trail_dist
                # Ratchet SL up with trailing stop
                if trailing_trigger_price > sl_price:
                    sl_price = trailing_trigger_price
                    pos["stop_loss_price"] = sl_price
                    changed = True

        if changed:
            database.upsert_position(pos)

        # 5. Evaluate Exit Triggers in Priority Order
        # A. Trailing Stop Exit (Fires only if armed and price fell below trailing trigger)
        if trailing_active and trailing_trigger_price > 0.0 and current_price <= trailing_trigger_price:
            reason = f"Trailing Stop hit: Price fell to {current_price:.4f} from peak {peak_price:.4f} (+{gain_pct:.2f}%)"
            return ("TRAILING_STOP", reason, gain_pct)

        # B. Breakeven Ratchet Stop (+0.3% fee lock floor)
        if ratcheted and sl_price > 0.0 and current_price <= sl_price and current_price >= entry_price:
            reason = f"Breakeven Ratchet Stop hit: Price fell to {current_price:.4f} (Stop: {sl_price:.4f}, +{gain_pct:.2f}%)"
            return ("BREAKEVEN_STOP", reason, gain_pct)

        # C. Stop Loss (Dynamic ATR SL / Hard Stop Floor)
        if sl_price > 0.0 and current_price <= sl_price:
            reason = f"Stop-Loss hit: Price fell to {current_price:.4f} (Stop: {sl_price:.4f}, {gain_pct:.2f}%)"
            return ("HARD_STOP_LOSS", reason, gain_pct)

        if gain_pct <= -2.5:
            reason = f"Max Loss Cap hit: Price dropped {gain_pct:.2f}% (Limit: -2.5%)"
            return ("HARD_STOP_LOSS", reason, gain_pct)

        # D. Dynamic Take-Profit Target
        if tp_price > 0.0 and current_price >= tp_price:
            reason = f"Dynamic ATR Take-Profit hit: Price reached {current_price:.4f} (Target: {tp_price:.4f}, +{gain_pct:.2f}%)"
            return ("TAKE_PROFIT", reason, gain_pct)

        if not is_trend_or_swing and gain_pct >= 2.5:
            reason = f"MR Take-Profit Target reached: +{gain_pct:.2f}% (Target: +2.5%)"
            return ("TAKE_PROFIT", reason, gain_pct)

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
        # Winning / Profitable trades: 90m cooldown (5400s) to allow market consolidation after profit taking
        # Losing / Stopped-out trades: 4h cooldown (14400s) to prevent revenge trading or knife-catching
        is_profitable = (realized_pnl > 0) or (pnl_pct > 0)
        if is_profitable:
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
