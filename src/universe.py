"""
Universe screening and Macro BTC Crash Gate interface.
Provides compatibility for 'src.universe' and re-exports screener functionality.
"""
from src.screener import TopMoverScreener
from src.strategy import check_btc_macro_gate

__all__ = ["TopMoverScreener", "check_btc_macro_gate"]
