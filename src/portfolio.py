import logging
import ccxt
from typing import Dict, Any, Optional
from .config import DRY_RUN

logger = logging.getLogger(__name__)

class PortfolioManager:
    """
    Manages portfolio balances, equity calculations, and simulated dry-run state.
    Provides complete decoupling between live Binance wallet state and simulated paper execution.
    """
    def __init__(self, exchange: Optional[ccxt.binance] = None):
        self.exchange = exchange
        self.quote_currency = 'USDC'
        self.ticker_cache: Dict[str, float] = {}
        self._last_known_equity = 30.0
        
        # Clean default paper state (Single Quote: 30 USDC + 0.005 BNB reserve; NEVER duplicate USDT)
        self.dry_run_state: Dict[str, Dict[str, float]] = {
            'USDC': {'free': 30.0, 'used': 0.0, 'total': 30.0},
            'BNB':  {'free': 0.005, 'used': 0.0, 'total': 0.005}
        }
        
        # If live exchange credentials are valid, seed initial dry_run_state from real wallet
        self._try_seed_dry_run_from_live()

    def _try_seed_dry_run_from_live(self):
        """
        Seeds paper trading balance from genuine live Binance balance if exchange keys exist.
        Ensures paper simulations accurately reflect real account capital ($24.72 USDC + $3.84 BNB).
        """
        if not self.exchange:
            return
        # Check if exchange has API keys configured
        has_keys = getattr(self.exchange, 'apiKey', None) and getattr(self.exchange, 'secret', None)
        if not has_keys:
            return
            
        try:
            live = self.fetch_live_balances()
            if live and any(b.get('total', 0) > 0 for b in live.values()):
                # Copy live balances into dry_run_state
                seeded = {}
                for asset, bal in live.items():
                    seeded[asset] = {
                        'free': float(bal.get('free', 0.0)),
                        'used': float(bal.get('used', 0.0)),
                        'total': float(bal.get('total', 0.0))
                    }
                self.dry_run_state = seeded
                logger.info(f"Initialized paper trading state seeded from live Binance wallet: {list(seeded.keys())}")
        except Exception as e:
            logger.debug(f"Could not seed paper balance from live exchange on startup: {e}")

    def fetch_live_balances(self) -> Dict[str, Dict[str, float]]:
        """
        Fetches genuine spot balances from Binance exchange via CCXT.
        Extracts clean asset structures and filters out zero/dust records.
        """
        if not self.exchange:
            return {}
        try:
            raw = self.exchange.fetch_balance()
            clean_balances = {}
            
            # CCXT returns 'total' dict containing all currencies
            total_dict = raw.get('total', {}) if isinstance(raw, dict) else {}
            free_dict = raw.get('free', {}) if isinstance(raw, dict) else {}
            used_dict = raw.get('used', {}) if isinstance(raw, dict) else {}
            
            for asset, total_amt in total_dict.items():
                if total_amt and total_amt > 0.0000001:
                    free_amt = free_dict.get(asset, total_amt)
                    used_amt = used_dict.get(asset, 0.0)
                    clean_balances[asset] = {
                        'free': float(free_amt or 0.0),
                        'used': float(used_amt or 0.0),
                        'total': float(total_amt)
                    }
                    
            return clean_balances
        except Exception as e:
            logger.error(f"Failed to fetch live balances from exchange: {e}")
            return {}

    def fetch_paper_balances(self) -> Dict[str, Dict[str, float]]:
        """Returns isolated paper balances for simulation."""
        return self.dry_run_state

    def fetch_balances(self) -> Dict[str, Dict[str, float]]:
        """Returns active balances based on DRY_RUN mode."""
        if DRY_RUN:
            return self.fetch_paper_balances()
        return self.fetch_live_balances()

    def get_total_equity_usd(self, balances: Optional[Dict[str, Any]] = None) -> float:
        """
        Calculates total portfolio equity in USD equivalent.
        Efficiently converts non-USD assets using cached tickers and filters out dust.
        """
        if balances is None:
            balances = self.fetch_balances()
            
        if not balances:
            if self._last_known_equity > 0:
                logger.warning(f"Calculated zero equity from empty balances. Preserving last known equity: ${self._last_known_equity:.2f}")
                return self._last_known_equity
            return 30.0
            
        total_usd = 0.0
        
        for asset, bal in balances.items():
            if not isinstance(bal, dict):
                continue
            amount = bal.get('total', bal.get('free', 0.0))
            if not amount or amount <= 0:
                continue
                
            # Direct 1:1 USD Stables
            if asset.upper() in ['USDC', 'USDT', 'USD', 'FDUSD', 'BUSD', 'DAI']:
                total_usd += float(amount)
                continue
                
            # Skip sub-cent dust (e.g. 0.0000001 coins) to avoid slow redundant network calls
            if asset.upper() == 'BTC' and amount < 0.000001:
                continue
                
            # Valuation via exchange ticker or cache
            val = self._convert_asset_to_usd(asset, float(amount))
            total_usd += val

        if total_usd > 0:
            self._last_known_equity = total_usd
        elif self._last_known_equity > 0:
            return self._last_known_equity

        return total_usd

    def _convert_asset_to_usd(self, asset: str, amount: float) -> float:
        """Helper to convert asset quantity to USD using USDC or USDT pairs."""
        if not self.exchange:
            # Unit test fallback
            if asset == 'BNB':
                return amount * 600.0
            elif asset == 'BTC':
                return amount * 80000.0
            return 0.0
            
        pairs_to_try = [f"{asset}/USDC", f"{asset}/USDT"]
        
        # Check cache first
        for pair in pairs_to_try:
            if pair in self.ticker_cache and self.ticker_cache[pair] > 0:
                return amount * self.ticker_cache[pair]
                
        # Fetch ticker if not in cache
        for pair in pairs_to_try:
            try:
                ticker = self.exchange.fetch_ticker(pair)
                price = float(ticker.get('last') or ticker.get('close') or 0.0)
                if price > 0:
                    self.ticker_cache[pair] = price
                    return amount * price
            except Exception:
                continue
                
        return 0.0

    def get_live_equity_usd(self) -> float:
        """
        Directly calculates live Binance account equity even in DRY_RUN mode.
        Allows dashboard and reports to display genuine Binance wallet value.
        """
        live_bals = self.fetch_live_balances()
        if not live_bals:
            return 0.0
        return self.get_total_equity_usd(live_bals)

    def get_available_quote_balance(self, quote: str = 'USDC') -> float:
        """Returns spendable free quote balance."""
        balances = self.fetch_balances()
        return balances.get(quote, {}).get('free', 0.0)

    def deduct_fee(self, asset: str, amount: float):
        """Simulate fee deduction in dry-run mode."""
        if DRY_RUN and asset in self.dry_run_state:
            self.dry_run_state[asset]['free'] = max(0.0, self.dry_run_state[asset]['free'] - amount)
            self.dry_run_state[asset]['total'] = max(0.0, self.dry_run_state[asset]['total'] - amount)

    def simulate_trade(self, symbol: str, side: str, amount: float, price: float, fee: float, quote: str):
        """Updates paper simulated balances on order execution."""
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
            self.dry_run_state[base]['free'] = max(0.0, self.dry_run_state[base]['free'] - amount)
            self.dry_run_state[base]['total'] = max(0.0, self.dry_run_state[base]['total'] - amount)
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
            
        if not self.exchange:
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
