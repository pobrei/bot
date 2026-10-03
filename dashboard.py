import streamlit as st
import os
import glob
import pandas as pd
from PIL import Image
import plotly.express as px
from src.config import (
    SYMBOLS, 
    DRY_RUN, 
    CAPITAL_PER_TRADE, 
    TIMEFRAME,
    TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_DISTANCE,
    HARD_STOP_LOSS,
    TAKE_PROFIT_TARGET,
    DYNAMIC_SCREENER
)
from src.position_tracker import PositionTracker
from src.market_data import MarketData
from src.portfolio import PortfolioManager
from src import database

@st.cache_data(ttl=60)
def get_portfolio_data():
    """Cached CCXT data — polls Binance REST at most once per 60 seconds.
    Prevents rate-limit bans (HTTP 429/418) on rapid Streamlit re-renders."""
    md = MarketData()
    pf = PortfolioManager(md.exchange)
    return pf.fetch_balances(), pf.get_total_equity_usd()

st.set_page_config(page_title="Binance Confluence Bot", layout="wide", page_icon="📈")

st.title("📈 Binance Algorithmic Trading Bot")

market_data = MarketData()
portfolio = PortfolioManager(market_data.exchange)
tracker = PositionTracker()

open_positions = tracker.get_open_positions()
trade_history = tracker.get_trade_history()
balances, total_equity = get_portfolio_data()

# Circuit breaker tracking
snapshots = database.get_equity_snapshots(24)
if snapshots:
    max_equity_24h = max([s['equity'] for s in snapshots])
    drawdown = (max_equity_24h - total_equity) / max_equity_24h if max_equity_24h > 0 else 0
    pnl_24h_pct = ((total_equity - snapshots[0]['equity']) / snapshots[0]['equity']) * 100 if snapshots[0]['equity'] > 0 else 0.0
else:
    max_equity_24h = total_equity
    drawdown = 0.0
    pnl_24h_pct = ((total_equity - 30.0) / 30.0) * 100 # Default baseline

cb_active = drawdown >= 0.05 or total_equity < 20.0

# Determine quote asset from configured symbols
quote_sample = SYMBOLS[0].split('/')[1] if SYMBOLS and '/' in SYMBOLS[0] else 'USDC'
currency_symbol = "€" if quote_sample == "EUR" else "$"

# Sidebar Configuration
st.sidebar.header("⚙️ Bot Configuration")
st.sidebar.write(f"**Execution Mode:** {'🟡 DRY RUN' if DRY_RUN else '🟢 LIVE TRADING'}")
st.sidebar.write(f"**Capital Per Trade:** {currency_symbol}{CAPITAL_PER_TRADE} {quote_sample}")
st.sidebar.write(f"**Timeframe:** {TIMEFRAME}")
st.sidebar.write(f"**Dynamic Screener:** {'Active' if DYNAMIC_SCREENER else 'Disabled'}")

st.sidebar.markdown("---")
st.sidebar.header("🚨 Emergency Controls")
is_killed = database.get_kill_switch()

if is_killed:
    st.sidebar.error("KILL-SWITCH ACTIVE! Trading paused.")
    if st.sidebar.button("▶️ RESUME TRADING", type="primary"):
        database.set_kill_switch(False)
        st.rerun()
else:
    st.sidebar.success("Bot is actively scanning and trading.")
    if st.sidebar.button("🛑 EMERGENCY STOP", type="primary"):
        database.set_kill_switch(True)
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("🛡️ Circuit Breakers & Risk")
if cb_active:
    st.sidebar.error("CIRCUIT BREAKER TRIGGERED!")
else:
    st.sidebar.success("Circuit Breaker: OK")
st.sidebar.write(f"**Current Drawdown:** {drawdown*100:.2f}%")
st.sidebar.write(f"**Live Equity:** {currency_symbol}{total_equity:.2f}")

st.sidebar.markdown("---")
st.sidebar.header("🛡️ Strategy Engine")
st.sidebar.write(f"**Hard Stop-Loss:** -{HARD_STOP_LOSS}%")
st.sidebar.write(f"**Trailing Stop Activation:** +{TRAILING_STOP_ACTIVATION}%")
st.sidebar.write(f"**Trailing Distance:** {TRAILING_STOP_DISTANCE}% from peak")
st.sidebar.write(f"**Take-Profit Target:** +{TAKE_PROFIT_TARGET}%")

# Top Metrics Row
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Total Equity", f"{currency_symbol}{total_equity:.2f}")
with col2:
    st.metric("24h PnL", f"{pnl_24h_pct:+.2f}%")
with col3:
    st.metric("Active Positions", f"{len(open_positions)}/2")
with col4:
    total_pnl = sum(t.get("realized_pnl", 0.0) for t in trade_history)
    st.metric("Cumulative PnL", f"{currency_symbol}{total_pnl:+.4f}")
with col5:
    win_trades = [t for t in trade_history if t.get("realized_pnl", 0) > 0]
    win_rate = (len(win_trades) / len(trade_history) * 100) if trade_history else 0.0
    st.metric("Win Rate", f"{win_rate:.1f}%")

st.markdown("---")

col_left, col_right = st.columns([1, 2])
with col_left:
    st.subheader("🥧 Portfolio Breakdown")
    pie_data = []
    for asset, bal in balances.items():
        if isinstance(bal, dict) and bal.get('total', 0) > 0:
            pie_data.append({"Asset": asset, "Amount": bal['total']})
    
    if pie_data:
        df_pie = pd.DataFrame(pie_data)
        fig = px.pie(df_pie, values='Amount', names='Asset', hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No balances available.")

with col_right:
    # Active Positions Section
    st.subheader("💼 Active Positions & Trailing Stops")
    if open_positions:
        pos_list = []
        for sym, pos in open_positions.items():
            pos_list.append({
                "Symbol": sym,
                "Entry Price": f"{pos['entry_price']:.4f}",
                "Highest Peak": f"{pos.get('highest_price', pos['entry_price']):.4f}",
                "Position Size": f"{pos['amount']} {sym.split('/')[0]}",
                "Trailing Stop Active": "🟢 YES" if pos.get("trailing_active") else "⚪ Pending (+1.5%)",
                "Entry Time": pos.get("entry_time", "N/A")
            })
        st.table(pd.DataFrame(pos_list))
    else:
        st.info("No active open positions. The bot is actively screening for confluence breakout setups.")

# Trade History Section
if trade_history:
    st.subheader("📜 Recent Trade History")
    hist_df = pd.DataFrame(trade_history)[['symbol', 'entry_time', 'exit_time', 'entry_price', 'exit_price', 'pnl_pct', 'realized_pnl', 'reason']]
    hist_df.rename(columns={
        'symbol': 'Pair',
        'entry_time': 'Entry',
        'exit_time': 'Exit',
        'entry_price': 'Buy Price',
        'exit_price': 'Sell Price',
        'pnl_pct': 'Gain %',
        'realized_pnl': f'PnL ({quote_sample})',
        'reason': 'Exit Reason'
    }, inplace=True)
    st.dataframe(hist_df, use_container_width=True)

st.markdown("---")
st.subheader("📊 Live Market Scans & Indicators")
st.write("Real-time candlestick charts with EMA 12/26 and Bollinger overlays generated during scanning.")

# Dynamically find all generated graph files
graph_files = glob.glob(f"graphs/*_{TIMEFRAME}.png")
if not graph_files:
    # Fallback to configured symbols
    graph_files = [f"graphs/{s.replace('/', '_')}_{TIMEFRAME}.png" for s in SYMBOLS]

cols = st.columns(2)
for idx, filepath in enumerate(sorted(graph_files)):
    basename = os.path.basename(filepath)
    symbol_display = basename.replace(f"_{TIMEFRAME}.png", "").replace("_", "/")
    
    col = cols[idx % 2]
    with col:
        st.write(f"### {symbol_display}")
        if os.path.exists(filepath):
            try:
                img = Image.open(filepath)
                st.image(img, width='stretch')
            except Exception as e:
                st.error(f"Error loading image: {e}")
        else:
            st.info(f"Scanning market data for {symbol_display}...")
