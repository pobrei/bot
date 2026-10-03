import ccxt
import time
import logging
from .config import DRY_RUN, CAPITAL_PER_TRADE, CAPITAL_EUR

logger = logging.getLogger(__name__)

class ExecutionManager:
    def __init__(self, exchange: ccxt.binance):
        self.exchange = exchange
        self.dry_run = DRY_RUN
        self.capital = CAPITAL_PER_TRADE

    def execute_order(self, symbol: str, side: str):
        try:
            base, quote = symbol.split('/') if '/' in symbol else (symbol, 'USD')
            market = self.exchange.market(symbol)
            min_cost = market.get('limits', {}).get('cost', {}).get('min', 5.0)
            
            if self.capital < min_cost:
                logger.warning(f"Capital {self.capital} {quote} is below minimum order cost {min_cost} {quote}. Order rejected.")
                return

            ticker = self.exchange.fetch_ticker(symbol)
            price = ticker['last']
            
            if not price:
                logger.error(f"Could not fetch latest price for {symbol} execution.")
                return

            if side == 'buy':
                amount = self.capital / price
                amount = float(self.exchange.amount_to_precision(symbol, amount))
                
                if self.dry_run:
                    fee = self.capital * 0.001
                    logger.info(f"[DRY RUN] BUY {amount} {symbol} @ {price} | Value: {self.capital} {quote} | Est. Fee: {fee} {quote}")
                    return {'status': 'dry_run', 'side': 'buy', 'price': price, 'amount': amount, 'quote': quote}

                # Live balance check to prevent InsufficientFunds
                try:
                    balance = self.exchange.fetch_balance()
                    avail = balance.get(quote, {}).get('free', 0.0) if isinstance(balance, dict) else 100.0
                    if avail < min_cost:
                        logger.warning(f"Insufficient {quote} balance ({avail:.2f} {quote}) to execute BUY order of {self.capital} {quote}.")
                        return None
                except Exception as e:
                    logger.warning(f"Could not fetch balance for pre-check: {e}")

                logger.info(f"[LIVE] Submitting market BUY order for {amount} {symbol} (~{self.capital} {quote})...")
                order = self.exchange.create_market_order(symbol, 'buy', amount)
                logger.info(f"BUY order successful: {order}")
                return {'status': 'live', 'side': 'buy', 'price': price, 'amount': amount, 'quote': quote, 'order': order}

            elif side == 'sell':
                amount = self.capital / price
                amount = float(self.exchange.amount_to_precision(symbol, amount))

                if self.dry_run:
                    fee = self.capital * 0.001
                    logger.info(f"[DRY RUN] SELL {amount} {symbol} @ {price} | Value: {self.capital} {quote} | Est. Fee: {fee} {quote}")
                    return {'status': 'dry_run', 'side': 'sell', 'price': price, 'amount': amount, 'quote': quote}

                # In live spot trading, we can only sell what we hold
                try:
                    balance = self.exchange.fetch_balance()
                    avail_base = balance.get(base, {}).get('free', 0.0) if isinstance(balance, dict) else 0.0
                    amount = float(self.exchange.amount_to_precision(symbol, avail_base))
                    if (amount * price) < min_cost:
                        logger.info(f"No active position to SELL for {symbol} (holding {avail_base} {base} worth < {min_cost} {quote}). Skipping.")
                        return None
                except Exception as e:
                    logger.warning(f"Could not fetch balance for sell pre-check: {e}")

                logger.info(f"[LIVE] Submitting market SELL order for {amount} {symbol}...")
                order = self.exchange.create_market_order(symbol, 'sell', amount)
                logger.info(f"SELL order successful: {order}")
                return {'status': 'live', 'side': 'sell', 'price': price, 'amount': amount, 'quote': quote, 'order': order}
            
        except (ccxt.NetworkError, ccxt.ExchangeError, ccxt.InsufficientFunds) as e:
            logger.error(f"Order execution failed for {symbol}: {e}")
            return None
            # Here we might implement further retries for execution specifically, but for now we log and fail safely.
