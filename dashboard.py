import streamlit as st
import streamlit.components.v1 as components
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
from src import database
from src.visualization import (
    create_interactive_candlestick, 
    create_equity_curve_chart, 
    get_tradingview_widget_html
)

st.set_page_config(page_title="Binance Confluence Bot", layout="wide", page_icon="📈")

st.title("📈 Binance Algorithmic Trading Bot")

tracker = PositionTracker()

open_positions = tracker.get_open_positions()
trade_history = tracker.get_trade_history()

# Read decoupled state from SQLite entirely
total_equity, balances = database.get_portfolio_state()

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

# Interactive 24h Equity Curve & Risk Boundaries
with st.expander("📈 Interactive Portfolio Performance & Risk Boundaries", expanded=True):
    fig_equity = create_equity_curve_chart(snapshots, total_equity, starting_capital=30.0)
    st.plotly_chart(fig_equity, use_container_width=True)

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
        fig.update_layout(
            paper_bgcolor='#0e1117',
            plot_bgcolor='#131722',
            font=dict(color='#D1D4DC'),
            margin=dict(l=20, r=20, t=30, b=20)
        )
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
    st.dataframe(hist_df, width='stretch')

st.markdown("---")

# ==========================================
# INTERACTIVE MARKET CHARTS & SIGNALS
# ==========================================
st.subheader("📊 Live Interactive Market Charts & Signals")
st.caption("Zero CCXT API quota consumption: Market data cached locally in SQLite by the bot engine, plus live TradingView streaming.")

cached_symbols = database.get_cached_market_symbols()
available_symbols = list(dict.fromkeys(cached_symbols + SYMBOLS))

chart_mode = st.radio(
    "Display Mode",
    ["🎯 Focus View (Interactive Deep-Dive)", "🔲 Multi-Chart Grid (All Pairs)"],
    horizontal=True
)

if chart_mode == "🎯 Focus View (Interactive Deep-Dive)":
    c_sel, c_view = st.columns([2, 3])
    with c_sel:
        selected_symbol = st.selectbox("Select Asset", available_symbols, index=0)
    with c_view:
        view_source = st.radio(
            "Chart Engine",
            ["🤖 Bot Strategy Engine (Plotly)", "🌐 TradingView Terminal (Live Stream)"],
            horizontal=True
        )

    candle_data = database.get_market_candles(selected_symbol, TIMEFRAME)

    if view_source == "🤖 Bot Strategy Engine (Plotly)":
        if candle_data is not None:
            df_candles, regime, updated_at = candle_data
            
            # Extract indicator badges
            curr = df_candles.iloc[-1]
            c_price = curr['close']
            ema12 = curr.get('EMA_12', None)
            ema26 = curr.get('EMA_26', None)
            rsi = curr.get('RSI_14', None)

            # Metrics pills
            mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
            with mcol1:
                st.metric("Latest Price", f"{c_price:,.4f} {quote_sample}")
            with mcol2:
                reg_icon = "🚀" if regime == "TRENDING" else ("↔️" if regime == "RANGING" else "⚙️")
                st.metric("Regime", f"{reg_icon} {regime}")
            with mcol3:
                if ema12 is not None and ema26 is not None:
                    ema_diff = ema12 - ema26
                    ema_status = "🟢 Bullish" if ema_diff > 0 else "🔴 Bearish"
                    st.metric("EMA Trend", ema_status, f"{ema_diff:+.2f}")
                else:
                    st.metric("EMA Trend", "Calculating...")
            with mcol4:
                if rsi is not None:
                    rsi_status = "⚠️ Overbought" if rsi > 70 else ("🟢 Oversold" if rsi < 30 else "⚪ Neutral")
                    st.metric("RSI (14)", f"{rsi:.1f}", rsi_status)
                else:
                    st.metric("RSI (14)", "Calculating...")
            with mcol5:
                st.metric("Last Scan", updated_at.split('T')[1][:8] if 'T' in updated_at else updated_at)

            # Render Plotly interactive chart
            fig = create_interactive_candlestick(selected_symbol, df_candles, regime, TIMEFRAME)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': True, 'scrollZoom': True})
        else:
            # Fallback to static graph if SQLite cache has not yet run for this symbol
            safe_sym = selected_symbol.replace('/', '_')
            static_file = f"graphs/{safe_sym}_{TIMEFRAME}.png"
            if os.path.exists(static_file):
                st.info(f"Showing static snapshot for {selected_symbol}. Interactive data will populate on the next bot scan cycle.")
                st.image(Image.open(static_file), width='stretch')
            else:
                st.info(f"Waiting for first scan cycle of {selected_symbol}... Please check back in a few seconds.")
    else:
        # Live TradingView Web Terminal
        tv_html = get_tradingview_widget_html(selected_symbol, TIMEFRAME, height=540)
        components.html(tv_html, height=555)

else:
    # Multi-Chart Grid View
    cols = st.columns(2)
    for idx, sym in enumerate(available_symbols):
        col = cols[idx % 2]
        with col:
            st.markdown(f"#### {sym}")
            candle_data = database.get_market_candles(sym, TIMEFRAME)
            if candle_data is not None:
                df_candles, regime, _ = candle_data
                fig = create_interactive_candlestick(sym, df_candles, regime, TIMEFRAME)
                # Keep height compact for grid view
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            else:
                safe_sym = sym.replace('/', '_')
                static_file = f"graphs/{safe_sym}_{TIMEFRAME}.png"
                if os.path.exists(static_file):
                    st.image(Image.open(static_file), width='stretch')
                else:
                    st.info(f"Scanning market data for {sym}...")
