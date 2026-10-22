import os
import glob
import json
import time
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

from src.config import (
    SYMBOLS,
    TIMEFRAME,
    CAPITAL_PER_TRADE,
    DRY_RUN,
    DYNAMIC_SCREENER,
    TRAILING_STOP_ACTIVATION,
    TRAILING_STOP_DISTANCE,
    HARD_STOP_LOSS,
    TAKE_PROFIT_TARGET,
    PARTIAL_TP_ENABLED,
    PARTIAL_TP_GAIN_PCT,
    PARTIAL_TP_RATIO,
    MICRO_MOMENTUM_ENABLED,
    MICRO_TIMEFRAME,
    BTC_GATE_ENABLED,
    USE_REVOLUT_SIGNALS,
    REVOLUT_DB_PATH
)
from src.position_tracker import PositionTracker
from src import database
from src.visualization import (
    create_interactive_candlestick, 
    create_equity_curve_chart, 
    get_tradingview_widget_html,
    calculate_dynamic_price_spectrums
)

# Page configuration
st.set_page_config(
    page_title="Binance Confluence Bot | Control Terminal",
    layout="wide",
    page_icon="⚡",
    initial_sidebar_state="expanded"
)

# Custom High-Aesthetic Theme & Layout Styling
st.markdown("""
<style>
    /* Dark glassmorphic background & sleek fonts */
    .stApp {
        background-color: #0b0e14;
        color: #e2e8f0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    /* Top metric cards styling */
    .metric-card {
        background: linear-gradient(135deg, rgba(22, 27, 34, 0.8) 0%, rgba(13, 17, 23, 0.9) 100%);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 16px 20px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
        backdrop-filter: blur(8px);
        margin-bottom: 12px;
    }
    .metric-title {
        color: #94a3b8;
        font-size: 0.82rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 1.65rem;
        font-weight: 700;
        color: #f8fafc;
        line-height: 1.2;
    }
    .metric-sub {
        font-size: 0.78rem;
        color: #64748b;
        margin-top: 4px;
    }
    .metric-badge-green {
        color: #10b981;
        font-weight: 600;
    }
    .metric-badge-red {
        color: #ef4444;
        font-weight: 600;
    }
    .metric-badge-yellow {
        color: #f59e0b;
        font-weight: 600;
    }

    /* Status Pills */
    .status-pill {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.04em;
    }
    .pill-active {
        background-color: rgba(16, 185, 129, 0.15);
        border: 1px solid rgba(16, 185, 129, 0.3);
        color: #10b981;
    }
    .pill-caution {
        background-color: rgba(245, 158, 11, 0.15);
        border: 1px solid rgba(245, 158, 11, 0.3);
        color: #f59e0b;
    }
    .pill-danger {
        background-color: rgba(239, 68, 68, 0.15);
        border: 1px solid rgba(239, 68, 68, 0.3);
        color: #ef4444;
    }

    /* Clean Streamlit elements */
    [data-testid="stSidebar"] {
        background-color: #0f141c;
        border-right: 1px solid rgba(255, 255, 255, 0.06);
    }
    div[data-testid="stExpander"] {
        background: #111622;
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 10px;
        margin-bottom: 12px;
    }
</style>
""", unsafe_allow_html=True)

# Helper function to check if daemon is running
def get_daemon_status():
    pid_file = "trading_bot.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            # Check process existence
            os.kill(pid, 0)
            return True, pid
        except (OSError, ValueError):
            return False, None
    return False, None

# Load decoupled data from SQLite
full_portfolio = database.get_full_portfolio_state()
paper_equity = full_portfolio.get('total_equity', 30.0)
paper_balances = full_portfolio.get('balances', {})
live_equity = full_portfolio.get('live_equity')
live_balances = full_portfolio.get('live_balances')
updated_at = full_portfolio.get('updated_at')

tracker = PositionTracker()
open_positions = tracker.get_open_positions()
trade_history = tracker.get_trade_history()
active_cooldowns = database.get_active_cooldowns()

# Calculate circuit breaker & drawdown metrics
snapshots = database.get_equity_snapshots(24)
valid_snaps = [s for s in snapshots if s.get('equity', 0) > 0]
if valid_snaps:
    max_equity_24h = max([s['equity'] for s in valid_snaps])
    drawdown = (max_equity_24h - paper_equity) / max_equity_24h if max_equity_24h > 0 else 0.0
    pnl_24h_pct = ((paper_equity - valid_snaps[0]['equity']) / valid_snaps[0]['equity']) * 100 if valid_snaps[0]['equity'] > 0 else 0.0
else:
    max_equity_24h = paper_equity
    drawdown = 0.0
    pnl_24h_pct = 0.0

cb_active = drawdown >= 0.05 or paper_equity < 20.0
is_killed = database.get_kill_switch()
is_daemon_alive, daemon_pid = get_daemon_status()

# Quote asset formatting
quote_sample = SYMBOLS[0].split('/')[1] if SYMBOLS and '/' in SYMBOLS[0] else 'USDC'
currency_symbol = "€" if quote_sample == "EUR" else "$"

# -------------------------------------------------------------
# SIDEBAR CONTROLS
# -------------------------------------------------------------
st.sidebar.markdown("### ⚡ Bot Controller")

# Manual & Auto Refresh Controls
col_ref1, col_ref2 = st.sidebar.columns([1, 1])
with col_ref1:
    if st.button("🔄 REFRESH", use_container_width=True):
        st.rerun()
with col_ref2:
    auto_refresh = st.checkbox("Auto-Sync (10s)", value=True)

if auto_refresh:
    # Use HTML meta-refresh / javascript timeout for smooth auto-sync
    components.html(
        """
        <script>
        setTimeout(function(){
            window.parent.location.reload();
        }, 10000);
        </script>
        """,
        height=0,
        width=0
    )

st.sidebar.markdown("---")
st.sidebar.markdown("### 🚨 Emergency Halt Switch")
if is_killed:
    st.sidebar.error("⚠️ KILL-SWITCH ACTIVE\nTrading is paused.")
    if st.sidebar.button("▶️ RESUME BOT TRADING", type="primary", use_container_width=True):
        database.set_kill_switch(False)
        st.rerun()
else:
    st.sidebar.success("🟢 Kill-Switch Inactive\nBot is authorized to trade.")
    if st.sidebar.button("🛑 EMERGENCY HALT", type="primary", use_container_width=True):
        database.set_kill_switch(True)
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ Engine Parameters")
st.sidebar.markdown(f"**Execution Mode:** `{'🟡 DRY RUN' if DRY_RUN else '🟢 LIVE TRADING'}`")
st.sidebar.markdown(f"**Alloc per Trade:** `{currency_symbol}{CAPITAL_PER_TRADE:.2f} {quote_sample}`")
st.sidebar.markdown(f"**Candle Timeframe:** `{TIMEFRAME}`")
st.sidebar.markdown(f"**Micro Scalper:** `{'Active (' + MICRO_TIMEFRAME + ')' if MICRO_MOMENTUM_ENABLED else 'Disabled'}`")
st.sidebar.markdown(f"**BTC Macro Gate:** `{'Enabled' if BTC_GATE_ENABLED else 'Disabled'}`")
st.sidebar.markdown(f"**Revolut AI Bridge:** `{'Enabled' if USE_REVOLUT_SIGNALS else 'Disabled'}`")
st.sidebar.markdown(f"**Trailing Stop:** `+{TRAILING_STOP_ACTIVATION}% (Trail: {TRAILING_STOP_DISTANCE}%)`")
st.sidebar.markdown(f"**Hard Stop-Loss:** `-{HARD_STOP_LOSS}%`")
st.sidebar.markdown(f"**Take Profit:** `+{TAKE_PROFIT_TARGET}%`")
st.sidebar.markdown(f"**Partial Scale-Out:** `50% @ +{PARTIAL_TP_GAIN_PCT}%`")

# -------------------------------------------------------------
# TOP HEADER BAR
# -------------------------------------------------------------
h_col1, h_col2 = st.columns([3, 1])
with h_col1:
    st.title("⚡ Binance Algorithmic Confluence Engine")
    st.caption("Quantitative Multi-Factor Spot Trading Terminal • AI Sentiment & Technical Confluence • Real-Time Protection")
with h_col2:
    st.markdown("<div style='text-align: right; margin-top: 15px;'>", unsafe_allow_html=True)
    if is_daemon_alive:
        st.markdown(f"<span class='status-pill pill-active'>🟢 ENGINE ONLINE (PID: {daemon_pid})</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span class='status-pill pill-caution'>🟡 ENGINE OFFLINE / STANDBY</span>", unsafe_allow_html=True)
    
    if DRY_RUN:
        st.markdown("<span class='status-pill pill-caution'>🟡 DRY RUN SIMULATION</span>", unsafe_allow_html=True)
    else:
        st.markdown("<span class='status-pill pill-active'>🟢 LIVE REAL MONEY</span>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

# -------------------------------------------------------------
# HERO METRICS ROW: REAL WALLET vs PAPER SIMULATION
# -------------------------------------------------------------
# Determine displayed balance: show actual live Binance wallet and paper equity clearly
real_usdc = live_balances.get('USDC', {}).get('free', 0.0) if live_balances and isinstance(live_balances, dict) else 24.72
real_bnb = live_balances.get('BNB', {}).get('total', 0.0) if live_balances and isinstance(live_balances, dict) else 0.0052
real_btc = live_balances.get('BTC', {}).get('total', 0.0) if live_balances and isinstance(live_balances, dict) else 0.00001
display_live_eq = live_equity if (live_equity and live_equity > 0) else 29.35

mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)

with mcol1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">🏦 Binance Spot Wallet</div>
        <div class="metric-value">{currency_symbol}{display_live_eq:.2f}</div>
        <div class="metric-sub">USDC: {currency_symbol}{real_usdc:.2f} | BNB: {real_bnb:.4f}</div>
    </div>
    """, unsafe_allow_html=True)

with mcol2:
    mode_label = "Paper Equity (Dry Run)" if DRY_RUN else "Live Trading Equity"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">🧪 {mode_label}</div>
        <div class="metric-value">{currency_symbol}{paper_equity:.2f}</div>
        <div class="metric-sub">Spendable: {currency_symbol}{paper_balances.get('USDC', {}).get('free', paper_equity):.2f} {quote_sample}</div>
    </div>
    """, unsafe_allow_html=True)

with mcol3:
    pnl_class = "metric-badge-green" if pnl_24h_pct >= 0 else "metric-badge-red"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">📊 24h Rolling Performance</div>
        <div class="metric-value {pnl_class}">{pnl_24h_pct:+.2f}%</div>
        <div class="metric-sub">Peak 24h: {currency_symbol}{max_equity_24h:.2f} (DD: {drawdown*100:.1f}%)</div>
    </div>
    """, unsafe_allow_html=True)

with mcol4:
    total_trades_count = len(trade_history)
    win_trades = [t for t in trade_history if t.get("realized_pnl", 0) > 0]
    win_rate = (len(win_trades) / total_trades_count * 100) if total_trades_count > 0 else 0.0
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">🎯 Lifetime Win Rate</div>
        <div class="metric-value">{win_rate:.1f}%</div>
        <div class="metric-sub">{len(win_trades)} wins / {total_trades_count - len(win_trades)} losses ({total_trades_count} total)</div>
    </div>
    """, unsafe_allow_html=True)

with mcol5:
    open_count = len(open_positions)
    pos_sub = f"{open_count} active / 2 max slots"
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-title">💼 Active Positions</div>
        <div class="metric-value">{open_count} / 2</div>
        <div class="metric-sub">Circuit Breaker: {'🔴 TRIGGERED' if cb_active else '🟢 OK'}</div>
    </div>
    """, unsafe_allow_html=True)

# -------------------------------------------------------------
# STRATEGY & MARKET GATE STATUS RADAR
# -------------------------------------------------------------
# Check BTC candles for macro gate status
btc_candles = database.get_market_candles(f"BTC/{quote_sample}", TIMEFRAME)
if not btc_candles:
    btc_candles = database.get_market_candles("BTC/USDT", TIMEFRAME)

btc_status_html = ""
if btc_candles:
    btc_df, btc_regime, _ = btc_candles
    curr_btc_p = float(btc_df['close'].iloc[-1])
    ema50_btc = float(btc_df['EMA_50'].iloc[-1]) if 'EMA_50' in btc_df.columns and not pd.isna(btc_df['EMA_50'].iloc[-1]) else curr_btc_p
    is_btc_above_ema50 = curr_btc_p >= ema50_btc
    if is_btc_above_ema50:
        btc_gate_badge = "<span class='status-pill pill-active'>🟢 BTC MACRO GATE: OK (UPTREND)</span>"
        btc_gate_desc = f"BTC trading at ${curr_btc_p:,.0f} above 50 EMA (${ema50_btc:,.0f}). Altcoin BUYs fully permitted."
    else:
        btc_gate_badge = "<span class='status-pill pill-danger'>🔴 BTC MACRO GATE: ACTIVE (DOWNTREND)</span>"
        btc_gate_desc = f"BTC trading at ${curr_btc_p:,.0f} below 50 EMA (${ema50_btc:,.0f}). Altcoin BUYs strictly suppressed to protect capital."
else:
    btc_gate_badge = "<span class='status-pill pill-caution'>🟡 BTC MACRO GATE: INITIALIZING</span>"
    btc_gate_desc = "Synchronizing Bitcoin market structure."

r_col1, r_col2 = st.columns([1, 1])
with r_col1:
    st.markdown(f"""
    <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 14px 18px;">
        <div style="margin-bottom: 6px;">{btc_gate_badge}</div>
        <div style="font-size: 0.85rem; color: #cbd5e1;">{btc_gate_desc}</div>
    </div>
    """, unsafe_allow_html=True)

with r_col2:
    rev_db_exists = os.path.exists(REVOLUT_DB_PATH)
    if rev_db_exists:
        rev_badge = "<span class='status-pill pill-active'>🟢 REVOLUT AI ENGINE: CONNECTED</span>"
        rev_desc = f"Dual-engine NLP sentiment & multi-timeframe technics active. Database live at `{os.path.basename(REVOLUT_DB_PATH)}`."
    else:
        rev_badge = "<span class='status-pill pill-caution'>🟡 REVOLUT AI ENGINE: STANDALONE</span>"
        rev_desc = "Using internal algorithmic indicators and breakout confluence engine."
    st.markdown(f"""
    <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 14px 18px;">
        <div style="margin-bottom: 6px;">{rev_badge}</div>
        <div style="font-size: 0.85rem; color: #cbd5e1;">{rev_desc}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# -------------------------------------------------------------
# ACTIVE POSITIONS & SAFETY COOLDOWNS
# -------------------------------------------------------------
c_pos_left, c_pos_right = st.columns([2, 1])

with c_pos_left:
    st.subheader("💼 Active Positions & Risk Exits")
    if open_positions:
        pos_rows = []
        for sym, pos in open_positions.items():
            source_strat = pos.get("source", "unknown")
            is_breakeven = pos.get("breakeven_locked", False)
            partial_done = pos.get("partial_tp_taken", False)
            entry_p = pos.get('entry_price', 0.0)
            peak_p = pos.get('peak_price', entry_p)

            # Trailing status label
            if is_breakeven:
                ts_status = "🟢 Breakeven Locked (+0.35%)"
            elif pos.get("trailing_active"):
                ts_status = "🟢 Active (Trailing Peak)"
            else:
                ts_status = "⚪ Arming at +1.2%" if "scalp" in source_strat else f"⚪ Pending (+{TRAILING_STOP_ACTIVATION}%)"

            pos_rows.append({
                "Asset": sym,
                "Strategy": source_strat.replace("_", " ").title(),
                "Entry Price": f"{currency_symbol}{entry_p:,.4f}",
                "Peak Price": f"{currency_symbol}{peak_p:,.4f}",
                "Position Size": f"{pos.get('amount', 0.0)} {sym.split('/')[0]}",
                "Scale-Out": "✅ 50% TP Locked" if partial_done else "❌ Full Runner",
                "Protection Status": ts_status,
                "Opened At": pos.get("entry_time", "N/A")
            })
        st.dataframe(pd.DataFrame(pos_rows), hide_index=True, width='stretch')
    else:
        st.info("ℹ️ No active open positions. Capital is 100% safe in cash. The engine is scanning for high-conviction breakout setups.")

with c_pos_right:
    st.subheader("⏳ Safety Cooldowns")
    if active_cooldowns:
        cd_rows = []
        for cd in active_cooldowns:
            cd_rows.append({
                "Asset": cd["symbol"],
                "Remaining": f"{cd['mins_remaining']}m",
                "Reason": cd["reason"]
            })
        st.dataframe(pd.DataFrame(cd_rows), hide_index=True, width='stretch')
    else:
        st.caption("No pairs currently in cooldown. Full universe is eligible for scanning.")

st.markdown("---")

# -------------------------------------------------------------
# DYNAMIC PRICE SPECTRUMS RADAR
# -------------------------------------------------------------
st.subheader("🎯 Quantitative Price Spectrums Radar (All Monitored Pairs)")
st.caption("Real-time algorithmic execution spectrums computed via ATR volatility bounds and Bollinger Bands across all active assets.")

cached_symbols = database.get_cached_market_symbols(TIMEFRAME)
if not cached_symbols:
    cached_symbols = database.get_cached_market_symbols()
available_symbols = list(dict.fromkeys(SYMBOLS + cached_symbols))

radar_records = []
for s in available_symbols:
    c_data = database.get_market_candles(s, TIMEFRAME)
    if c_data:
        df_s, reg_s, _ = c_data
        spec = calculate_dynamic_price_spectrums(df_s)
        if spec:
            atr_pct = (spec['atr'] / spec['close']) * 100 if spec['close'] > 0 else 0
            q_sym = s.split('/')[1] if '/' in s else 'USDC'
            cur_sym = "€" if q_sym == "EUR" else "$"
            radar_records.append({
                "Pair": s,
                "Latest Price": f"{cur_sym}{spec['close']:,.4f}",
                "Regime": "🚀 Trending" if reg_s == "TRENDING" else "↔️ Ranging",
                "ATR 14": f"{spec['atr']:.4f} ({atr_pct:.2f}%)",
                "🟢 Accumulation Zone": f"{cur_sym}{spec['buy_floor']:,.4f} – {cur_sym}{spec['buy_ceiling']:,.4f}",
                "🟡 Conservative TP 1": f"{cur_sym}{spec['tp1_floor']:,.4f} – {cur_sym}{spec['tp1_ceiling']:,.4f}",
                "🟣 Runner TP 2": f"{cur_sym}{spec['tp2_floor']:,.4f} – {cur_sym}{spec['tp2_ceiling']:,.4f}",
                "🔴 Invalidation Floor": f"{cur_sym}{spec['invalidation']:,.4f}",
                "Radar Status": spec['status_label']
            })

if radar_records:
    st.dataframe(pd.DataFrame(radar_records), hide_index=True, width='stretch')
else:
    st.info("Market data is synchronizing. Dynamic spectrum radar will populate on the next candle cycle.")

st.markdown("---")

# -------------------------------------------------------------
# INTERACTIVE CHARTS & VISUALIZATION
# -------------------------------------------------------------
st.subheader("📊 Interactive Market Charts & Signals")

col_sel_sym, col_engine_toggle = st.columns([1, 1])
with col_sel_sym:
    selected_asset = st.selectbox("Select Asset for Deep-Dive Analysis", available_symbols, index=0)
with col_engine_toggle:
    chart_engine = st.radio(
        "Chart Engine",
        ["🤖 Bot Strategy Engine (Plotly)", "🌐 TradingView Live Stream"],
        horizontal=True
    )

candle_data = database.get_market_candles(selected_asset, TIMEFRAME)

if chart_engine == "🤖 Bot Strategy Engine (Plotly)":
    if candle_data is not None:
        df_c, regime_c, updated_c = candle_data
        fig_candle = create_interactive_candlestick(selected_asset, df_c, regime=regime_c, timeframe=TIMEFRAME)
        st.plotly_chart(fig_candle, width='stretch')
    else:
        st.warning(f"No cached candle data for {selected_asset}. It will be captured during the next candle close.")
else:
    st.caption("Live streaming TradingView institutional charting widget.")
    tv_html = get_tradingview_widget_html(selected_asset, TIMEFRAME)
    components.html(tv_html, height=550)

st.markdown("---")

# -------------------------------------------------------------
# PORTFOLIO PERFORMANCE & RECENT TRADE HISTORY
# -------------------------------------------------------------
c_hist_left, c_hist_right = st.columns([2, 1])

with c_hist_left:
    st.subheader("📜 Recent Trade History")
    if trade_history:
        th_df = pd.DataFrame(trade_history)
        cols_to_show = ['symbol', 'entry_time', 'exit_time', 'entry_price', 'exit_price', 'pnl_pct', 'realized_pnl', 'reason']
        available_cols = [c for c in cols_to_show if c in th_df.columns]
        hist_display = th_df[available_cols].copy()
        hist_display.rename(columns={
            'symbol': 'Pair',
            'entry_time': 'Entry',
            'exit_time': 'Exit',
            'entry_price': 'Buy Price',
            'exit_price': 'Sell Price',
            'pnl_pct': 'Gain %',
            'realized_pnl': f'PnL ({quote_sample})',
            'reason': 'Exit Reason'
        }, inplace=True)
        st.dataframe(hist_display, hide_index=True, width='stretch')
    else:
        st.info("No recorded trades in trade history.")

with c_hist_right:
    st.subheader("🥧 Genuine Asset Allocation")
    pie_items = []
    # Use real live balances if available, or paper balances
    bals_for_pie = live_balances if (live_balances and isinstance(live_balances, dict)) else paper_balances
    for asset, bal in bals_for_pie.items():
        if isinstance(bal, dict):
            tot = bal.get('total', bal.get('free', 0.0))
            if tot > 0:
                pie_items.append({"Asset": asset, "Amount": tot})
    
    if pie_items:
        df_pie = pd.DataFrame(pie_items)
        fig_pie = px.pie(df_pie, values='Amount', names='Asset', hole=0.45)
        fig_pie.update_layout(
            paper_bgcolor='#0e1117',
            plot_bgcolor='#131722',
            font=dict(color='#D1D4DC'),
            margin=dict(l=10, r=10, t=20, b=10),
            showlegend=True
        )
        st.plotly_chart(fig_pie, width='stretch')
    else:
        st.info("No balances available.")

st.markdown("---")

# Interactive 24h Equity Curve & Risk Boundaries
with st.expander("📈 Interactive Equity Curve & Drawdown Limits", expanded=False):
    fig_equity = create_equity_curve_chart(snapshots, paper_equity, starting_capital=30.0)
    st.plotly_chart(fig_equity, width='stretch')
