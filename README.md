# Binance Algorithmic Trading Bot 📈

An advanced, production-grade cryptocurrency trading bot built with Python, CCXT, and Streamlit. Features dynamic multi-timeframe analysis, ADX market regime switching, automated top-mover screening, trailing stop-losses, and news sentiment analysis.

## Key Features

- **Dynamic Market Regime Switcher**: Uses the Average Directional Index (ADX 14) + Directional Movement (`DMP`/`DMN`) to distinguish between trending and ranging markets:
  - **Trending (ADX ≥ 25)**: Fast EMA crosses (12/26), MACD momentum expansion, and trend continuation.
  - **Ranging (ADX < 20)**: Mean-reversion entries at Lower Bollinger Band bounces and oversold RSI.
- **Dynamic Trailing Stop & Profit Engine**:
  - Trailing stop arms automatically once in profit (+1.5%), following 1.0% below the peak price.
  - Hard stop-loss (-2.5%) for capital defense.
  - Take-profit targets (+5.0%).
- **Top-Mover Breakout Screener**: Continuously scans liquid Binance spot pairs for 24h volume anomalies and momentum breakouts, augmenting the core basket (`BTC`, `ETH`, `SOL`, `BNB`).
- **Live Streamlit Dashboard**: Real-time KPI metrics, active positions table, trade history, and automated candlestick charts with technical overlays.
- **Safety & Risk Guards**:
  - Pre-flight balance checks before submitting orders.
  - Single-position capital limits to prevent over-allocation.
  - Spot market execution only (no leverage or margin borrowing).
  - Default `DRY_RUN=True` mode for safe offline simulation.

---

## Project Structure

```
├── .agents/                # Agent workflows & trading safety rules
├── src/
│   ├── config.py           # Environment and trading settings
│   ├── execution.py        # CCXT order execution with live balance checks
│   ├── market_data.py      # Resilient OHLCV fetching with exponential backoff
│   ├── position_tracker.py # Trailing stop, hard stop, and trade history engine
│   ├── screener.py         # Dynamic Binance top-gainers & volume screener
│   ├── sentiment.py        # RSS news sentiment analysis (VADER)
│   ├── strategy.py         # ADX regime switcher & multi-indicator confluence
│   └── visualization.py    # Candlestick chart generation (mplfinance)
├── tests/
│   └── test_bot.py         # Comprehensive unit tests
├── dashboard.py            # Streamlit web interface
├── main.py                 # Core trading daemon loop
├── .env.example            # Environment variable template
└── requirements.txt        # Project dependencies
```

---

## Quickstart

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/pobrei/bot.git
cd bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and configure your API keys:

```bash
cp .env.example .env
```

Edit `.env`:
```ini
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
DRY_RUN=True                    # Set to False for live execution
SYMBOLS=BTC/USDC,ETH/USDC,SOL/USDC,BNB/USDC
TRADE_AMOUNT=5.5                # Amount per trade
TIMEFRAME=1h
DYNAMIC_SCREENER=True
TRAILING_STOP_ACTIVATION=1.5
TRAILING_STOP_DISTANCE=1.0
HARD_STOP_LOSS=2.5
TAKE_PROFIT_TARGET=5.0
```

### 3. Run Unit Tests

```bash
PYTHONPATH=. pytest tests/
```

### 4. Launch Trading Daemon

```bash
python main.py
```

### 5. Launch Live Dashboard

```bash
streamlit run dashboard.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Disclaimer

This software is for educational and research purposes. Cryptocurrency trading involves substantial risk of loss. Always test in DRY RUN mode before executing with real capital.
