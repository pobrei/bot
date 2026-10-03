# Quantitative Risk Rules

This rule defines institutional-grade risk parameters for the trading bot.

## Capital Parameters
- **Baseline Starting Capital**: $30.00 total equity across assets.
- **Dynamic Sizing**: Maximum 33% of total equity per trade position.
- **Minimum Notional Floor**: Strictly clamped to a minimum notional of $5.50 (to clear Binance `minNotional` filters).
- **Available Balance Constraint**: Sizing must account for available free quote balance and open fee buffers. Avoid `InsufficientFunds` errors.

## Whitelist & Concurrency
- **Market Whitelist**: Only allow scanning and trading for liquid pairs: `['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']`. 
- **Max Concurrent Positions**: Strict limit of 2 active trading positions at any time.

## Fee Optimization
- **BNB Reserve**: Maintain a ~$3 BNB reserve for exchange fee discounts. Do not sell or trade this reserve.

## Hard Circuit Breakers
- **Maximum Daily Drawdown**: 5.0% maximum rolling 24h equity drawdown limit.
- **Absolute Equity Floor**: $20.00 total equity.
- **Kill-Switch Enforcement**: If either circuit breaker is breached, the bot must halt all trading and cancel open orders immediately.
