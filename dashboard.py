import streamlit as st
import streamlit.components.v1 as components
import os
import glob
import pandas as pd
from PIL import Image
import plotly.express as px
from dotenv import load_dotenv
load_dotenv(override=True)

# Read dynamic live values from environment / .env
_symbols_str = os.getenv("SYMBOLS", "BTC/USDC,ETH/USDC,SOL/USDC,BNB/USDC,XRP/USDC,DOGE/USDC,SUI/USDC,AVAX/USDC")
SYMBOLS = [s.strip() for s in _symbols_str.split(",") if s.strip()]
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
CAPITAL_PER_TRADE = float(os.getenv("TRADE_AMOUNT", os.getenv("CAPITAL_EUR", "6.5")))
DRY_RUN = os.getenv("DRY_RUN", "False").lower() in ("true", "1", "t", "yes")
DYNAMIC_SCREENER = os.getenv("DYNAMIC_SCREENER", "True").lower() in ("true", "1", "t", "yes")
TRAILING_STOP_ACTIVATION = float(os.getenv("TRAILING_STOP_ACTIVATION", "1.5"))
TRAILING_STOP_DISTANCE = float(os.getenv("TRAILING_STOP_DISTANCE", "1.0"))
HARD_STOP_LOSS = float(os.getenv("HARD_STOP_LOSS", "2.5"))
TAKE_PROFIT_TARGET = float(os.getenv("TAKE_PROFIT_TARGET", "5.0"))

import plotly.graph_objects as go
from src.position_tracker import PositionTracker
from src import database
from src.visualization import (
    create_interactive_candlestick, 
    create_equity_curve_chart, 
    get_tradingview_widget_html,
    calculate_dynamic_price_spectrums
)
from src.paper_verification import EventDrivenPaperEngine

st.set_page_config(page_title="Binance Confluence Bot", layout="wide", page_icon="📈")

st.title("📈 Binance Algorithmic Trading Bot")

# Top controls / manual refresh
if st.sidebar.button("🔄 REFRESH LIVE DATA", use_container_width=True):
    st.rerun()

tracker = PositionTracker()

open_positions = tracker.get_open_positions()
trade_history = tracker.get_trade_history()

# Read decoupled state from SQLite entirely
total_equity, balances = database.get_portfolio_state()

# Circuit breaker tracking
snapshots = database.get_equity_snapshots(24)
valid_snaps = [s for s in snapshots if s.get('equity', 0) > 0]
if valid_snaps:
    max_equity_24h = max([s['equity'] for s in valid_snaps])
    drawdown = (max_equity_24h - total_equity) / max_equity_24h if max_equity_24h > 0 else 0
    pnl_24h_pct = ((total_equity - valid_snaps[0]['equity']) / valid_snaps[0]['equity']) * 100 if valid_snaps[0]['equity'] > 0 else 0.0
else:
    max_equity_24h = total_equity
    drawdown = 0.0
    pnl_24h_pct = 0.0

cb_active = drawdown >= 0.05 or total_equity < 20.0

# Determine quote asset from configured symbols
quote_sample = SYMBOLS[0].split('/')[1] if SYMBOLS and '/' in SYMBOLS[0] else 'USDC'
currency_symbol = "€" if quote_sample == "EUR" else "$"

# Sidebar Configuration
st.sidebar.header("⚙️ Bot Configuration")
st.sidebar.write(f"**Execution Mode:** {'🟡 DRY RUN' if DRY_RUN else '🟢 LIVE TRADING'}")
st.sidebar.write(f"**Capital Per Trade:** {currency_symbol}{CAPITAL_PER_TRADE:.2f} {quote_sample}")
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
            source_strat = pos.get("source", "unknown")
            ts_status = "🟢 1.2% from peak" if source_strat == "breakout" else ("🟢 YES" if pos.get("trailing_active") else "⚪ Pending (+1.5%)")
            pos_list.append({
                "Symbol": sym,
                "Strategy": source_strat.replace("_", " ").title(),
                "Entry Price": f"{pos['entry_price']:.4f}",
                "Highest Peak": f"{pos.get('peak_price', pos['entry_price']):.4f}",
                "Position Size": f"{pos['amount']} {sym.split('/')[0]}",
                "Trailing Stop": ts_status,
                "Entry Time": pos.get("entry_time", "N/A")
            })
        st.table(pd.DataFrame(pos_list))
    else:
        st.info("No active open positions. The bot is actively screening for confluence breakout setups.")

    # Active Cooldowns (Re-entry Protection)
    active_cooldowns = database.get_active_cooldowns()
    if active_cooldowns:
        st.markdown("##### ⏳ Active Safety Cooldowns (Re-entry Protection)")
        cd_list = []
        for cd in active_cooldowns:
            cd_list.append({
                "Symbol": cd["symbol"],
                "Cooldown Remaining": f"{cd['mins_remaining']} min",
                "Protection Reason": cd["reason"]
            })
        st.dataframe(pd.DataFrame(cd_list), hide_index=True, use_container_width=True)

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

cached_symbols = database.get_cached_market_symbols(TIMEFRAME)
if not cached_symbols:
    cached_symbols = database.get_cached_market_symbols()
available_symbols = list(dict.fromkeys(SYMBOLS + cached_symbols))

# Dynamic Price Spectrums Watchlist / Radar Table
with st.expander("🎯 Dynamic Price Spectrums Radar (All Monitored Pairs)", expanded=True):
    st.caption("Quantitative execution spectrums computed in real-time using ATR volatility offsets and Bollinger Bands across all active universe assets.")
    radar_rows = []
    for s in available_symbols:
        c_data = database.get_market_candles(s, TIMEFRAME)
        if c_data:
            df_s, reg_s, _ = c_data
            spec = calculate_dynamic_price_spectrums(df_s)
            if spec:
                atr_pct = (spec['atr'] / spec['close']) * 100 if spec['close'] > 0 else 0
                q_sym = s.split('/')[1] if '/' in s else 'USDC'
                cur_sym = "€" if q_sym == "EUR" else "$"
                radar_rows.append({
                    "Pair": s,
                    "Price": f"{cur_sym}{spec['close']:,.4f}",
                    "Regime": "🚀 Trending" if reg_s == "TRENDING" else "↔️ Ranging",
                    "ATR 14": f"{cur_sym}{spec['atr']:.4f} ({atr_pct:.2f}%)",
                    "🟢 Accumulation Range": f"{cur_sym}{spec['buy_floor']:,.4f} – {cur_sym}{spec['buy_ceiling']:,.4f}",
                    "🟡 Conservative TP 1": f"{cur_sym}{spec['tp1_floor']:,.4f} – {cur_sym}{spec['tp1_ceiling']:,.4f}",
                    "🟣 Runner TP 2": f"{cur_sym}{spec['tp2_floor']:,.4f} – {cur_sym}{spec['tp2_ceiling']:,.4f}",
                    "🔴 Invalidation Floor": f"{cur_sym}{spec['invalidation']:,.4f}",
                    "Status": spec['status_label']
                })
    if radar_rows:
        st.dataframe(pd.DataFrame(radar_rows), hide_index=True, width='stretch')
    else:
        st.info("Market data is synchronizing. Spectrum radar will populate on the next candle cycle.")

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

            # Dynamic Price Spectrums
            spectrums = calculate_dynamic_price_spectrums(df_candles)
            show_spectrums = st.toggle("🎯 Display Dynamic Price Spectrums (Accumulation / TP1 / TP2 / Invalidation)", value=True)

            # Render Plotly interactive chart with native Dynamic Price Spectrums
            fig = create_interactive_candlestick(selected_symbol, df_candles, regime, TIMEFRAME, show_spectrums=show_spectrums)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': True, 'scrollZoom': True})

            if spectrums:
                with st.expander("🎯 Dynamic Price Spectrums & ATR Execution Zones", expanded=True):
                    st.markdown(f"**Execution Status:** {spectrums['status_label']} &nbsp;|&nbsp; **Candle ATR (14):** `{spectrums['atr']:.4f} {quote_sample}` &nbsp;|&nbsp; **Last Close:** `{spectrums['close']:.4f} {quote_sample}`")
                    sp_col1, sp_col2, sp_col3, sp_col4 = st.columns(4)
                    with sp_col1:
                        st.metric(
                            "🟢 Accumulation Zone",
                            f"{currency_symbol}{spectrums['buy_floor']:,.4f} – {currency_symbol}{spectrums['buy_ceiling']:,.4f}",
                            "Entry Bounds"
                        )
                    with sp_col2:
                        tp1_gain = ((spectrums['tp1_floor'] - spectrums['close']) / spectrums['close']) * 100
                        st.metric(
                            "🟡 Conservative TP 1",
                            f"{currency_symbol}{spectrums['tp1_floor']:,.4f} – {currency_symbol}{spectrums['tp1_ceiling']:,.4f}",
                            f"+{tp1_gain:.1f}% Target"
                        )
                    with sp_col3:
                        tp2_gain = ((spectrums['tp2_floor'] - spectrums['close']) / spectrums['close']) * 100
                        st.metric(
                            "🟣 Runner TP 2",
                            f"{currency_symbol}{spectrums['tp2_floor']:,.4f} – {currency_symbol}{spectrums['tp2_ceiling']:,.4f}",
                            f"+{tp2_gain:.1f}% Expansion"
                        )
                    with sp_col4:
                        inv_dist = ((spectrums['invalidation'] - spectrums['close']) / spectrums['close']) * 100
                        st.metric(
                            "🔴 Invalidation Floor",
                            f"{currency_symbol}{spectrums['invalidation']:,.4f}",
                            f"{inv_dist:.1f}% Hard Stop",
                            delta_color="inverse"
                        )
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

# ==========================================
# EVENT-DRIVEN PAPER TRADING VERIFICATION
# ==========================================
st.markdown("---")
st.subheader("🧪 Event-Driven Paper Trading Verification Protocol")
st.caption("Rigorous quantitative backtest simulation with exact execution frictions (0.075% BNB commission, 0.05% slippage, 0.05% spread), Walk-Forward Analysis (70% In-Sample / 30% Out-of-Sample), and Probabilistic Sharpe Ratio (PSR).")

with st.expander("🔬 Launch Event-Driven Verification Engine", expanded=True):
    col_v1, col_v2, col_v3, col_v4, col_v5 = st.columns([2, 1, 1, 1, 2])
    with col_v1:
        verify_symbol = st.selectbox("Verification Asset", available_symbols, index=0, key="verify_sym_select")
    with col_v2:
        verify_limit = st.selectbox("Historical Window", [100, 300, 500, 1000], index=2, key="verify_window_select")
    with col_v3:
        verify_capital = st.number_input("Starting Capital ($)", min_value=10.0, max_value=1000.0, value=30.0, step=5.0, key="verify_cap_input")
    with col_v4:
        hurdle_choice = st.selectbox("ATR Hurdle", ["⚡ Scaled (0.3%)", "🛡️ Strict (1.5%)", "0.5%", "0.2%"], index=0, key="verify_hurdle_select")
        if "1.5%" in hurdle_choice:
            atr_hurdle_val = 0.015
        elif "0.5%" in hurdle_choice:
            atr_hurdle_val = 0.005
        elif "0.2%" in hurdle_choice:
            atr_hurdle_val = 0.002
        else:
            atr_hurdle_val = 0.003
    with col_v5:
        st.write("")
        st.write("")
        run_verify = st.button("▶️ RUN VERIFICATION SIMULATION", type="primary", use_container_width=True)

    if run_verify:
        with st.spinner(f"Simulating bar-by-bar execution for {verify_symbol} ({verify_limit} bars, ATR hurdle: {atr_hurdle_val*100:.1f}%)..."):
            df_v = None
            try:
                import ccxt
                ex = ccxt.binance({'enableRateLimit': True})
                ohlcv = ex.fetch_ohlcv(verify_symbol, timeframe=TIMEFRAME, limit=verify_limit)
                if ohlcv:
                    df_v = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_v['datetime'] = pd.to_datetime(df_v['timestamp'], unit='ms')
            except Exception as e:
                candle_data_v = database.get_market_candles(verify_symbol, TIMEFRAME)
                if candle_data_v:
                    df_v, _, _ = candle_data_v

            if df_v is not None and len(df_v) >= 50:
                engine = EventDrivenPaperEngine(
                    starting_capital=verify_capital, 
                    timeframe=TIMEFRAME,
                    min_atr_ratio=atr_hurdle_val
                )
                st.session_state["last_verification_results"] = engine.run_simulation(df_v, symbol=verify_symbol)
            else:
                st.warning(f"Insufficient candles available for {verify_symbol} (need at least 50).")

    if "last_verification_results" in st.session_state:
        res = st.session_state["last_verification_results"]
        s_sum = res['summary']
        s_is = res['in_sample']
        s_oos = res['out_of_sample']
        t_log = res['trade_log']
        eq_curve = res['equity_curve']
        v_sym = res['symbol']

        st.markdown(f"#### 📊 Verification Results: **{v_sym}** ({TIMEFRAME})")
        vm1, vm2, vm3, vm4, vm5 = st.columns(5)
        with vm1:
            st.metric("Net Realized PnL", f"{currency_symbol}{s_sum['net_pnl']:+.4f}", f"{s_sum['return_pct']:+.2f}%")
        with vm2:
            st.metric("Win Rate", f"{s_sum['win_rate_pct']:.1f}%", f"{s_sum['win_trades']}W / {s_sum['loss_trades']}L")
        with vm3:
            st.metric("Profit Factor", f"{s_sum['profit_factor']:.2f}", "Gross W / L")
        with vm4:
            st.metric("Max Drawdown", f"{s_sum['max_drawdown_pct']:.2f}%", "Peak-to-Trough")
        with vm5:
            psr_val = s_sum['psr']
            psr_status = "🟢 PASS (Edge Validated)" if psr_val >= 0.90 else ("🟡 MODERATE" if psr_val >= 0.70 else "🔴 LOW (Insufficient Edge)")
            st.metric("Probabilistic Sharpe (PSR)", f"{psr_val:.4f}", psr_status)

        # Plot Walk-Forward Equity Curve
        if eq_curve:
            df_ec = pd.DataFrame(eq_curve)
            fig_eq = go.Figure()
            is_part = df_ec[df_ec['segment'] == 'In-Sample']
            if not is_part.empty:
                fig_eq.add_trace(go.Scatter(
                    x=is_part['datetime'], y=is_part['equity'],
                    name="In-Sample Equity (70%)",
                    line=dict(color="#2962FF", width=2)
                ))
            oos_part = df_ec[df_ec['segment'] == 'Out-of-Sample']
            if not oos_part.empty:
                fig_eq.add_trace(go.Scatter(
                    x=oos_part['datetime'], y=oos_part['equity'],
                    name="Out-of-Sample Validation (30%)",
                    line=dict(color="#0ECB81", width=2.5)
                ))
            if not is_part.empty and not oos_part.empty:
                split_time = oos_part['datetime'].iloc[0]
                fig_eq.add_vline(x=split_time, line_dash="dash", line_color="#F0B90B", annotation_text="Walk-Forward Split")

            fig_eq.update_layout(
                title=f"Walk-Forward Equity Curve: {v_sym} ({TIMEFRAME})",
                template="plotly_dark",
                plot_bgcolor="#131722",
                paper_bgcolor="#0e1117",
                height=380,
                margin=dict(l=30, r=30, t=40, b=20)
            )
            st.plotly_chart(fig_eq, use_container_width=True)

        # WFA Edge Stability Table & Trade Log
        col_wf1, col_wf2 = st.columns([1, 2])
        with col_wf1:
            st.markdown("##### 🔬 Walk-Forward Edge Stability")
            wfa_df = pd.DataFrame([
                {"Metric": "Trades", "In-Sample (70%)": s_is['total_trades'], "Out-of-Sample (30%)": s_oos['total_trades']},
                {"Metric": "Win Rate", "In-Sample (70%)": f"{s_is['win_rate_pct']:.1f}%", "Out-of-Sample (30%)": f"{s_oos['win_rate_pct']:.1f}%"},
                {"Metric": "Return %", "In-Sample (70%)": f"{s_is['return_pct']:+.2f}%", "Out-of-Sample (30%)": f"{s_oos['return_pct']:+.2f}%"},
                {"Metric": "Profit Factor", "In-Sample (70%)": f"{s_is['profit_factor']:.2f}", "Out-of-Sample (30%)": f"{s_oos['profit_factor']:.2f}"},
                {"Metric": "Max Drawdown", "In-Sample (70%)": f"{s_is['max_drawdown_pct']:.2f}%", "Out-of-Sample (30%)": f"{s_oos['max_drawdown_pct']:.2f}%"}
            ])
            st.dataframe(wfa_df, hide_index=True, use_container_width=True)

        with col_wf2:
            st.markdown(f"##### 📜 Simulation Trade Execution Log ({len(t_log)} Trades)")
            if t_log:
                df_tl = pd.DataFrame(t_log)[['entry_time', 'exit_time', 'entry_price', 'exit_price', 'pnl_pct', 'realized_pnl', 'reason', 'segment']]
                df_tl.rename(columns={
                    'entry_time': 'Entry',
                    'exit_time': 'Exit',
                    'entry_price': 'Buy Price',
                    'exit_price': 'Sell Price',
                    'pnl_pct': 'Gain %',
                    'realized_pnl': f'PnL ({quote_sample})',
                    'reason': 'Trigger Reason',
                    'segment': 'Data Partition'
                }, inplace=True)
                st.dataframe(df_tl.tail(10), hide_index=True, use_container_width=True)
            else:
                st.info("No trades triggered during this historical window (strict ATR volatility hurdle / confluence filter).")
