import ccxt
import time
import logging
from typing import Optional, Callable, Any
from .config import DRY_RUN
from .portfolio import PortfolioManager

logger = logging.getLogger(__name__)

def retry_network_call(max_retries=3, backoff_factor=1.5):
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs) -> Any:
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (ccxt.NetworkError, ccxt.RateLimitExceeded) as e:
                    retries += 1
                    if retries >= max_retries:
                        logger.error(f"Network call failed after {max_retries} retries: {e}")
                        raise
                    delay = backoff_factor ** retries
                    logger.warning(f"Network/RateLimit error: {e}. Retrying in {delay:.2f}s...")
                    time.sleep(delay)
                except Exception as e:
                    # Don't retry on other errors (like InsufficientFunds)
                    raise
        return wrapper
    return decorator

class ExecutionManager:
    def __init__(self, exchange: ccxt.binance, portfolio: Optional[PortfolioManager] = None):
        self.exchange = exchange
        self.portfolio = portfolio
        self.dry_run = DRY_RUN
        self.capital = 0.0

    @retry_network_call()
    def _fetch_ticker(self, symbol: str):
        return self.exchange.fetch_ticker(symbol)

    @retry_network_call()
    def _create_market_order(self, symbol: str, side: str, amount: float):
        return self.exchange.create_market_order(symbol, side, amount)

    def execute_order(self, symbol: str, side: str, amount_usd: float = 0.0):
        try:
            base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USD')
            market = self.exchange.market(symbol)
            min_cost = market.get('limits', {}).get('cost', {}).get('min', 5.0)
            min_qty = market.get('limits', {}).get('amount', {}).get('min', 0.0)
            
            ticker = self._fetch_ticker(symbol)
            price = ticker['last']
            
            if not price:
                logger.error(f"Could not fetch latest price for {symbol} execution.")
                return None

            prec_price = self.exchange.price_to_precision(symbol, price)
            if isinstance(prec_price, (int, float, str)):
                try:
                    price = float(prec_price)
                except (ValueError, TypeError):
                    pass

            if amount_usd <= 0.0 and self.capital > 0.0:
                amount_usd = self.capital

            if side == 'buy':
                if amount_usd < min_cost:
                    logger.warning(f"Capital {amount_usd} {quote} is below minimum order cost {min_cost} {quote}. Order rejected.")
                    return None
                    
                amount = amount_usd / price
                amount = float(self.exchange.amount_to_precision(symbol, amount))
                
                # Check minQty LOT_SIZE filter
                if amount < min_qty:
                    logger.warning(f"Calculated amount {amount} is below minQty {min_qty}. Order rejected.")
                    return None

                # Re-check cost after precision adjustment for MIN_NOTIONAL filter
                final_cost = amount * price
                if final_cost < min_cost:
                    logger.warning(f"Precision-adjusted cost {final_cost} {quote} is below minimum {min_cost} {quote}. Order rejected.")
                    return None

                if self.dry_run:
                    fee = final_cost * 0.001
                    if self.portfolio:
                        self.portfolio.simulate_trade(symbol, 'buy', amount, price, fee, quote)
                    logger.info(f"[DRY RUN] BUY {amount} {symbol} @ {price} | Value: {final_cost} {quote} | Est. Fee: {fee} {quote}")
                    return {'status': 'dry_run', 'side': 'buy', 'price': price, 'amount': amount, 'quote': quote}

                # Live balance check to prevent InsufficientFunds
                if self.portfolio:
                    try:
                        avail = self.portfolio.get_available_quote_balance(quote)
                        if avail < final_cost:
                            logger.warning(f"Insufficient {quote} balance ({avail:.2f} {quote}) to execute BUY order of {final_cost} {quote}.")
                            return None
                    except Exception as e:
                        logger.warning(f"Could not fetch balance for pre-check: {e}")

                logger.info(f"[LIVE] Submitting market BUY order for {amount} {symbol} (~{final_cost} {quote})...")
                order = self._create_market_order(symbol, 'buy', amount)
                logger.info(f"BUY order successful: {order}")
                fill_price = price
                fill_amount = amount
                if isinstance(order, dict):
                    if order.get('average') and float(order['average']) > 0:
                        fill_price = float(order['average'])
                    elif order.get('price') and float(order['price']) > 0:
                        fill_price = float(order['price'])
                    if order.get('filled') and float(order['filled']) > 0:
                        fill_amount = float(order['filled'])
                return {'status': 'live', 'side': 'buy', 'price': fill_price, 'amount': fill_amount, 'quote': quote, 'order': order}

            elif side == 'sell':
                amount = 0.0
                if self.portfolio:
                    try:
                        balances = self.portfolio.fetch_balances()
                        avail_base = balances.get(base, {}).get('free', 0.0) if isinstance(balances, dict) else 0.0
                        
                        if avail_base <= 0.0:
                            logger.info(f"No active position to SELL for {symbol} (holding 0 {base}). Returning dust/cleared status.")
                            return {'status': 'cleared_dust', 'side': 'sell', 'price': price, 'amount': 0.0, 'quote': quote}
                            
                        amount = float(self.exchange.amount_to_precision(symbol, avail_base))
                        
                        if amount < min_qty:
                            logger.info(f"No active position to SELL for {symbol} (holding {amount} {base} < minQty {min_qty}). Returning dust/cleared status.")
                            return {'status': 'cleared_dust', 'side': 'sell', 'price': price, 'amount': 0.0, 'quote': quote}
                            
                        final_cost = amount * price
                        if final_cost < min_cost:
                            logger.info(f"No active position to SELL for {symbol} (holding {avail_base} {base} worth < minNotional {min_cost} {quote}). Returning dust/cleared status.")
                            return {'status': 'cleared_dust', 'side': 'sell', 'price': price, 'amount': 0.0, 'quote': quote}
                    except Exception as e:
                        logger.warning(f"Could not fetch balance for sell pre-check: {e}")
                        return None
                else:
                    sell_usd = amount_usd if amount_usd > 0 else (self.capital if self.capital > 0 else 5.0)
                    amount = float(self.exchange.amount_to_precision(symbol, sell_usd / price))
                    final_cost = amount * price

                if self.dry_run:
                    if final_cost < min_cost:
                        logger.info(f"[DRY RUN] SELL skipped: dust amount {amount} {base} worth {final_cost:.4f} {quote} < min {min_cost} {quote}.")
                        return None
                    fee = final_cost * 0.001
                    if self.portfolio:
                        self.portfolio.simulate_trade(symbol, 'sell', amount, price, fee, quote)
                    logger.info(f"[DRY RUN] SELL {amount} {symbol} @ {price} | Value: {final_cost} {quote} | Est. Fee: {fee} {quote}")
                    return {'status': 'dry_run', 'side': 'sell', 'price': price, 'amount': amount, 'quote': quote}

                logger.info(f"[LIVE] Submitting market SELL order for {amount} {symbol}...")
                try:
                    order = self._create_market_order(symbol, 'sell', amount)
                except ccxt.InsufficientFunds as e:
                    logger.warning(f"Insufficient funds for {amount} {symbol}. Attempting to sell 99%...")
                    try:
                        amount = float(self.exchange.amount_to_precision(symbol, avail_base * 0.99))
                        if amount < min_qty:
                            logger.info(f"99% of balance {amount} is less than minQty. Returning dust/cleared status.")
                            return {'status': 'cleared_dust', 'side': 'sell', 'price': price, 'amount': 0.0, 'quote': quote}
                        order = self._create_market_order(symbol, 'sell', amount)
                    except Exception as e2:
                        logger.error(f"Second sell attempt failed: {e2}")
                        return {'status': 'cleared_error', 'side': 'sell', 'price': price, 'amount': 0.0, 'quote': quote}
                logger.info(f"SELL order successful: {order}")
                fill_price = price
                fill_amount = amount
                if isinstance(order, dict):
                    if order.get('average') and float(order['average']) > 0:
                        fill_price = float(order['average'])
                    elif order.get('price') and float(order['price']) > 0:
                        fill_price = float(order['price'])
                    if order.get('filled') and float(order['filled']) > 0:
                        fill_amount = float(order['filled'])
                return {'status': 'live', 'side': 'sell', 'price': fill_price, 'amount': fill_amount, 'quote': quote, 'order': order}
            
        except ccxt.InsufficientFunds as e:
            if side == 'sell':
                logger.error(f"Order execution failed (InsufficientFunds) for {symbol}: {e}. Returning cleared status.")
                return {'status': 'cleared_error', 'side': 'sell', 'price': price if 'price' in locals() else 0.0, 'amount': 0.0, 'quote': quote if 'quote' in locals() else ''}
            logger.error(f"Order execution failed for {symbol}: {e}")
            return None
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            logger.error(f"Order execution failed for {symbol}: {e}")
            return None
