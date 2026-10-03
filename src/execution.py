import ccxt
import time
import logging
from .config import DRY_RUN
from .portfolio import PortfolioManager

logger = logging.getLogger(__name__)

class ExecutionManager:
    def __init__(self, exchange: ccxt.binance, portfolio: PortfolioManager):
        self.exchange = exchange
        self.portfolio = portfolio
        self.dry_run = DRY_RUN

    def execute_order(self, symbol: str, side: str, amount_usd: float = 0.0):
        try:
            base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USD')
            market = self.exchange.market(symbol)
            min_cost = market.get('limits', {}).get('cost', {}).get('min', 5.0)
            min_qty = market.get('limits', {}).get('amount', {}).get('min', 0.0)
            
            ticker = self.exchange.fetch_ticker(symbol)
            price = ticker['last']
            
            if not price:
                logger.error(f"Could not fetch latest price for {symbol} execution.")
                return None

            price = float(self.exchange.price_to_precision(symbol, price))

            if side == 'buy':
                if amount_usd < min_cost:
                    logger.warning(f"Order size {amount_usd} {quote} is below minimum order cost {min_cost} {quote}. Order rejected.")
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
                    self.portfolio.simulate_trade(symbol, 'buy', amount, price, fee, quote)
                    logger.info(f"[DRY RUN] BUY {amount} {symbol} @ {price} | Value: {final_cost} {quote} | Est. Fee: {fee} {quote}")
                    return {'status': 'dry_run', 'side': 'buy', 'price': price, 'amount': amount, 'quote': quote}

                # Live balance check to prevent InsufficientFunds
                try:
                    avail = self.portfolio.get_available_quote_balance(quote)
                    if avail < final_cost:
                        logger.warning(f"Insufficient {quote} balance ({avail:.2f} {quote}) to execute BUY order of {final_cost} {quote}.")
                        return None
                except Exception as e:
                    logger.warning(f"Could not fetch balance for pre-check: {e}")

                logger.info(f"[LIVE] Submitting market BUY order for {amount} {symbol} (~{final_cost} {quote})...")
                order = self.exchange.create_market_order(symbol, 'buy', amount)
                logger.info(f"BUY order successful: {order}")
                return {'status': 'live', 'side': 'buy', 'price': price, 'amount': amount, 'quote': quote, 'order': order}

            elif side == 'sell':
                # amount_usd is ignored for sell, we sell all held amount
                try:
                    balances = self.portfolio.fetch_balances()
                    avail_base = balances.get(base, {}).get('free', 0.0) if isinstance(balances, dict) else 0.0
                    amount = float(self.exchange.amount_to_precision(symbol, avail_base))
                    
                    if amount < min_qty:
                        logger.info(f"No active position to SELL for {symbol} (holding {amount} {base} < minQty {min_qty}). Skipping.")
                        return None
                        
                    final_cost = amount * price
                    if final_cost < min_cost:
                        logger.info(f"No active position to SELL for {symbol} (holding {avail_base} {base} worth < minNotional {min_cost} {quote}). Skipping.")
                        return None
                except Exception as e:
                    logger.warning(f"Could not fetch balance for sell pre-check: {e}")
                    return None

                if self.dry_run:
                    if final_cost < min_cost:
                        logger.info(f"[DRY RUN] SELL skipped: dust amount {amount} {base} worth {final_cost:.4f} {quote} < min {min_cost} {quote}.")
                        return None
                    fee = final_cost * 0.001
                    self.portfolio.simulate_trade(symbol, 'sell', amount, price, fee, quote)
                    logger.info(f"[DRY RUN] SELL {amount} {symbol} @ {price} | Value: {final_cost} {quote} | Est. Fee: {fee} {quote}")
                    return {'status': 'dry_run', 'side': 'sell', 'price': price, 'amount': amount, 'quote': quote}

                logger.info(f"[LIVE] Submitting market SELL order for {amount} {symbol}...")
                order = self.exchange.create_market_order(symbol, 'sell', amount)
                logger.info(f"SELL order successful: {order}")
                return {'status': 'live', 'side': 'sell', 'price': price, 'amount': amount, 'quote': quote, 'order': order}
            
        except (ccxt.NetworkError, ccxt.ExchangeError, ccxt.InsufficientFunds) as e:
            logger.error(f"Order execution failed for {symbol}: {e}")
            return None

