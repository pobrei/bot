import streamlit as st
import os
import glob
import pandas as pd
from PIL import Image
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

st.set_page_config(page_title="Binance Confluence Bot", layout="wide", page_icon="📈")

st.title("📈 Binance Algorithmic Trading Bot")

tracker = PositionTracker()
open_positions = tracker.get_open_positions()
trade_history = tracker.get_trade_history()

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
st.sidebar.header("🛡️ Risk & Profit Engine")
st.sidebar.write(f"**Hard Stop-Loss:** -{HARD_STOP_LOSS}%")
st.sidebar.write(f"**Trailing Stop Activation:** +{TRAILING_STOP_ACTIVATION}%")
st.sidebar.write(f"**Trailing Distance:** {TRAILING_STOP_DISTANCE}% from peak")
st.sidebar.write(f"**Take-Profit Target:** +{TAKE_PROFIT_TARGET}%")

# Top Metrics Row
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Open Positions", len(open_positions))
with col2:
    st.metric("Closed Trades", len(trade_history))
with col3:
    total_pnl = sum(t.get("realized_pnl", 0.0) for t in trade_history)
    st.metric("Cumulative PnL", f"{currency_symbol}{total_pnl:+.4f}")
with col4:
    win_trades = [t for t in trade_history if t.get("realized_pnl", 0) > 0]
    win_rate = (len(win_trades) / len(trade_history) * 100) if trade_history else 0.0
    st.metric("Win Rate", f"{win_rate:.1f}%")

st.markdown("---")

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
