import os
import json
import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List

from .config import (
    TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_DISTANCE,
    HARD_STOP_LOSS,
    TAKE_PROFIT_TARGET
)

logger = logging.getLogger(__name__)

POSITIONS_FILE = "positions.json"
HISTORY_FILE = "trade_history.json"

class PositionTracker:
    def __init__(self, positions_file: str = POSITIONS_FILE, history_file: str = HISTORY_FILE):
        self.positions_file = positions_file
        self.history_file = history_file
        self._ensure_files()

    def _ensure_files(self):
        if not os.path.exists(self.positions_file):
            with open(self.positions_file, 'w') as f:
                json.dump({}, f)
        if not os.path.exists(self.history_file):
            with open(self.history_file, 'w') as f:
                json.dump([], f)

    def get_open_positions(self) -> Dict[str, Any]:
        try:
            with open(self.positions_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {self.positions_file}: {e}")
            return {}

    def get_open_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        positions = self.get_open_positions()
        return positions.get(symbol)

    def record_entry(self, symbol: str, entry_price: float, amount: float, quote: str = "USDC") -> Dict[str, Any]:
        positions = self.get_open_positions()
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
        positions[symbol] = pos_data
        
        try:
            with open(self.positions_file, 'w') as f:
                json.dump(positions, f, indent=2)
            logger.info(f"[POSITION OPENED] {symbol} @ {entry_price:.4f} | Amount: {amount} {symbol.split('/')[0]}")
        except Exception as e:
            logger.error(f"Failed to save position entry: {e}")
            
        return pos_data

    def check_position_exit(self, symbol: str, current_price: float) -> Optional[Tuple[str, str, float]]:
        """
        Evaluates an open position against Stop-Loss, Trailing Stop, and Take-Profit rules.
        Returns: (exit_type, reason, pnl_pct) or None if holding.
        """
        positions = self.get_open_positions()
        if symbol not in positions:
            return None

        pos = positions[symbol]
        entry_price = pos["entry_price"]
        highest_price = pos.get("highest_price", entry_price)
        trailing_active = pos.get("trailing_active", False)

        # 1. Update highest price if reached new peak
        if current_price > highest_price:
            highest_price = current_price
            pos["highest_price"] = highest_price

        # 2. Calculate PnL percentage relative to entry
        gain_pct = ((current_price - entry_price) / entry_price) * 100.0

        # 3. Check Trailing Stop activation
        peak_gain_pct = ((highest_price - entry_price) / entry_price) * 100.0
        if peak_gain_pct >= TRAILING_STOP_ACTIVATION and not trailing_active:
            trailing_active = True
            pos["trailing_active"] = True
            logger.info(f"[{symbol}] Trailing Stop ACTIVATED! Peak Gain: +{peak_gain_pct:.2f}% (Threshold: {TRAILING_STOP_ACTIVATION}%)")

        # Save updated state
        positions[symbol] = pos
        try:
            with open(self.positions_file, 'w') as f:
                json.dump(positions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to update position highest price: {e}")

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
        positions = self.get_open_positions()
        if symbol not in positions:
            return None

        pos = positions.pop(symbol)
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

        # Update positions file
        try:
            with open(self.positions_file, 'w') as f:
                json.dump(positions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to remove closed position: {e}")

        # Append to trade history
        try:
            history = self.get_trade_history()
            history.append(trade_record)
            with open(self.history_file, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to record trade history: {e}")

        logger.info(f"[POSITION CLOSED] {symbol} @ {exit_price:.4f} | Reason: {reason} | PnL: {pnl_pct:+.2f}% ({realized_pnl:+.4f} {quote})")
        return trade_record

    def get_trade_history(self) -> List[Dict[str, Any]]:
        try:
            with open(self.history_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {self.history_file}: {e}")
            return []
