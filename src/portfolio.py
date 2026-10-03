import logging
import ccxt
from typing import Dict, Any
from .config import DRY_RUN

logger = logging.getLogger(__name__)

class PortfolioManager:
    def __init__(self, exchange: ccxt.binance):
        self.exchange = exchange
        self.quote_currency = 'USDC'  # Default to USDC but will adjust based on pairs
        
        # Simulated state for dry-run
        self.dry_run_state = {
            'USDC': {'free': 30.0, 'used': 0.0, 'total': 30.0},
            'USDT': {'free': 30.0, 'used': 0.0, 'total': 30.0},
            'BNB':  {'free': 0.005, 'used': 0.0, 'total': 0.005} # Simulated $3 BNB reserve
        }

    def fetch_balances(self) -> Dict[str, Dict[str, float]]:
        if DRY_RUN:
            return self.dry_run_state
        
        try:
            return self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"Failed to fetch live balances: {e}")
            return {}

    def get_total_equity_usd(self) -> float:
        balances = self.fetch_balances()
        total_usd = 0.0
        
        for asset, bal in balances.items():
            if not isinstance(bal, dict) or 'total' not in bal:
                continue
                
            amount = bal['total']
            if amount <= 0:
                continue
                
            if asset in ['USDT', 'USDC', 'USD']:
                total_usd += amount
            else:
                symbol = f"{asset}/USDC"
                try:
                    # In a real app we'd fetch multiple tickers efficiently.
                    # This fetches one by one which is ok for a small whitelist.
                    ticker = self.exchange.fetch_ticker(symbol)
                    price = ticker.get('last', 0.0)
                    total_usd += amount * price
                except:
                    # Fallback to USDT
                    try:
                        ticker = self.exchange.fetch_ticker(f"{asset}/USDT")
                        price = ticker.get('last', 0.0)
                        total_usd += amount * price
                    except:
                        pass
        return total_usd

    def get_available_quote_balance(self, quote: str = 'USDC') -> float:
        balances = self.fetch_balances()
        return balances.get(quote, {}).get('free', 0.0)

    def deduct_fee(self, asset: str, amount: float):
        """Simulate fee deduction in dry-run mode."""
        if DRY_RUN:
            if asset in self.dry_run_state and self.dry_run_state[asset]['free'] >= amount:
                self.dry_run_state[asset]['free'] -= amount
                self.dry_run_state[asset]['total'] -= amount
            else:
                # Deduct from BNB reserve if applicable (simplification)
                pass

    def simulate_trade(self, symbol: str, side: str, amount: float, price: float, fee: float, quote: str):
        if not DRY_RUN:
            return
            
        base = symbol.split('/')[0]
        cost = amount * price
        
        if base not in self.dry_run_state:
            self.dry_run_state[base] = {'free': 0.0, 'used': 0.0, 'total': 0.0}
        if quote not in self.dry_run_state:
            self.dry_run_state[quote] = {'free': 0.0, 'used': 0.0, 'total': 0.0}
            
        if side == 'buy':
            self.dry_run_state[quote]['free'] -= (cost + fee)
            self.dry_run_state[quote]['total'] -= (cost + fee)
            self.dry_run_state[base]['free'] += amount
            self.dry_run_state[base]['total'] += amount
        elif side == 'sell':
            self.dry_run_state[base]['free'] -= amount
            self.dry_run_state[base]['total'] -= amount
            self.dry_run_state[quote]['free'] += (cost - fee)
            self.dry_run_state[quote]['total'] += (cost - fee)
