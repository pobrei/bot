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
        self.ticker_cache = {}
        self._last_known_equity = 30.0

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
                    self.ticker_cache[symbol] = price
                    total_usd += amount * price
                except Exception as e:
                    # Fallback to cache, then USDT pair, then cache
                    if symbol in self.ticker_cache:
                        total_usd += amount * self.ticker_cache[symbol]
                        continue
                    try:
                        usdt_symbol = f"{asset}/USDT"
                        ticker = self.exchange.fetch_ticker(usdt_symbol)
                        price = ticker.get('last', 0.0)
                        self.ticker_cache[usdt_symbol] = price
                        total_usd += amount * price
                    except Exception as inner_e:
                        if f"{asset}/USDT" in self.ticker_cache:
                            total_usd += amount * self.ticker_cache[f"{asset}/USDT"]

        if total_usd > 0:
            self._last_known_equity = total_usd
        elif self._last_known_equity > 0:
            logger.warning(f"Calculated zero equity from balances. Falling back to last known equity: ${self._last_known_equity:.2f}")
            return self._last_known_equity

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

    def auto_rebalance_quote(self, min_quote_required: float = 6.0, target_replenish: float = 15.0) -> bool:
        """
        If free quote currency (USDC) is below min_quote_required,
        automatically checks if there is excess unmanaged BNB (> $8 worth)
        and sells just enough into quote currency to replenish capital.
        """
        if DRY_RUN:
            return False
            
        try:
            balances = self.fetch_balances()
            free_quote = balances.get('USDC', {}).get('free', 0.0)
            if free_quote >= min_quote_required:
                return False
                
            free_bnb = balances.get('BNB', {}).get('free', 0.0)
            ticker = self.exchange.fetch_ticker('BNB/USDC')
            bnb_price = ticker.get('last', 0.0)
            if not bnb_price or bnb_price <= 0:
                return False
                
            bnb_value_usd = free_bnb * bnb_price
            FEE_RESERVE_USD = 5.0  # Keep at least $5 of BNB for Binance trading fee discounts
            available_bnb_usd = bnb_value_usd - FEE_RESERVE_USD
            
            if available_bnb_usd >= min_quote_required:
                needed_usd = min(target_replenish, available_bnb_usd)
                amount_bnb = float(self.exchange.amount_to_precision('BNB/USDC', needed_usd / bnb_price))
                if amount_bnb >= 0.001 and (amount_bnb * bnb_price) >= 5.0:
                    logger.info(f"[AUTO-REBALANCE] Rebalancing {amount_bnb} BNB to USDC (~${amount_bnb * bnb_price:.2f}) to replenish quote balance...")
                    order = self.exchange.create_market_sell_order('BNB/USDC', amount_bnb)
                    logger.info(f"[AUTO-REBALANCE] Order filled: {order.get('id')}")
                    return True
        except Exception as e:
            logger.warning(f"[AUTO-REBALANCE] Failed to auto-rebalance: {e}")
        return False

