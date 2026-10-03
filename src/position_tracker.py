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

    def record_entry(self, symbol: str, entry_price: float, amount: float, quote: str = "USDC") -> Dict[str, Any]:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        pos_data = {
            "symbol": symbol,
            "entry_price": float(entry_price),
            "highest_price": float(entry_price),
            "amount": float(amount),
            "quote": quote,
            "trailing_active": False,
            "entry_time": now_str
        }
        
        database.upsert_position(pos_data)
        logger.info(f"[POSITION OPENED] {symbol} @ {entry_price:.4f} | Amount: {amount} {symbol.split('/')[0]}")
        
        # Webhook Notification
        msg = f"**Symbol:** {symbol}\n**Entry Price:** {entry_price:.4f} {quote}\n**Amount:** {amount}"
        send_webhook_alert(f"Position Opened: {symbol}", msg, color=65280) # Green
            
        return pos_data

    def check_position_exit(self, symbol: str, current_price: float) -> Optional[Tuple[str, str, float]]:
        """
        Evaluates an open position against Stop-Loss, Trailing Stop, and Take-Profit rules.
        Returns: (exit_type, reason, pnl_pct) or None if holding.
        """
        pos = database.get_open_position(symbol)
        if not pos:
            return None

        entry_price = pos["entry_price"]
        highest_price = pos.get("highest_price", entry_price)
        trailing_active = pos.get("trailing_active", False)

        changed = False
        # 1. Update highest price if reached new peak
        if current_price > highest_price:
            highest_price = current_price
            pos["highest_price"] = highest_price
            changed = True

        # 2. Calculate PnL percentage relative to entry
        gain_pct = ((current_price - entry_price) / entry_price) * 100.0

        # 3. Check Trailing Stop activation
        peak_gain_pct = ((highest_price - entry_price) / entry_price) * 100.0
        if peak_gain_pct >= TRAILING_STOP_ACTIVATION and not trailing_active:
            trailing_active = True
            pos["trailing_active"] = True
            changed = True
            logger.info(f"[{symbol}] Trailing Stop ACTIVATED! Peak Gain: +{peak_gain_pct:.2f}% (Threshold: {TRAILING_STOP_ACTIVATION}%)")

        if changed:
            database.upsert_position(pos)

        # 4. Check Hard Stop-Loss
        if gain_pct <= -HARD_STOP_LOSS:
            reason = f"Hard Stop-Loss hit: Price dropped {gain_pct:.2f}% (Limit: -{HARD_STOP_LOSS}%)"
            return ("HARD_STOP_LOSS", reason, gain_pct)

        # 5. Check Take-Profit Target
        if gain_pct >= TAKE_PROFIT_TARGET:
            reason = f"Take-Profit Target reached: +{gain_pct:.2f}% (Target: +{TAKE_PROFIT_TARGET}%)"
            return ("TAKE_PROFIT", reason, gain_pct)

        # 6. Check Trailing Stop Trigger (distance from peak)
        if trailing_active:
            trailing_trigger_price = highest_price * (1.0 - (TRAILING_STOP_DISTANCE / 100.0))
            if current_price <= trailing_trigger_price:
                reason = f"Trailing Stop hit: Price fell to {current_price:.4f} from peak {highest_price:.4f} (+{gain_pct:.2f}% locked in)"
                return ("TRAILING_STOP", reason, gain_pct)

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

        logger.info(f"[POSITION CLOSED] {symbol} @ {exit_price:.4f} | Reason: {reason} | PnL: {pnl_pct:+.2f}% ({realized_pnl:+.4f} {quote})")
        
        # Webhook Notification
        color = 65280 if realized_pnl > 0 else 16711680 # Green if profit, Red if loss
        msg = f"**Symbol:** {symbol}\n**Exit Price:** {exit_price:.4f} {quote}\n**PnL:** {pnl_pct:+.2f}% ({realized_pnl:+.4f} {quote})\n**Reason:** {reason}"
        send_webhook_alert(f"Position Closed: {symbol}", msg, color=color)
        
        return trade_record

    def get_trade_history(self) -> List[Dict[str, Any]]:
        return database.get_trade_history()

