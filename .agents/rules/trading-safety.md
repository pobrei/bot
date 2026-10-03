# Trading Safety Rules

- CAPITAL CONSTRAINT: Starting capital per trade is approximately $5 / €5. Multi-level grid trading, laddering, and margin/leverage are strictly forbidden. All entries must be single-position spot market orders that respect Binance MIN_NOTIONAL limits (minimum ~$5 order cost).
- DEFAULT SAFETY: The bot must run in DRY_RUN mode by default (`DRY_RUN=True`). Live order execution must only be enabled by explicit user flag override.
- ERROR HANDLING: All exchange calls must be wrapped with CCXT-specific exception handlers (NetworkError, ExchangeError, InsufficientFunds) with exponential backoff retries.
- PAIR SPECIFICATION: Target pairs are liquid quoted assets (e.g. BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, or EUR equivalents) on the 1-hour to 4-hour timeframe. This provides daily trade opportunities across multiple assets without succumbing to fee erosion from low-timeframe micro-scalping.
