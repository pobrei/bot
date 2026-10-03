import mplfinance as mpf
import pandas as pd
import logging
import os
from typing import List, Dict, Any, Optional
import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger(__name__)

def create_interactive_candlestick(
    symbol: str, 
    df: pd.DataFrame, 
    regime: str = "UNKNOWN", 
    timeframe: str = "1h"
) -> go.Figure:
    """
    Builds a professional interactive candlestick chart with EMA overlays,
    Bollinger Bands, Volume, and RSI subplots using Plotly.
    """
    df_plot = df.copy()
    if 'timestamp' in df_plot.columns:
        if pd.api.types.is_numeric_dtype(df_plot['timestamp']):
            df_plot['datetime'] = pd.to_datetime(df_plot['timestamp'], unit='ms')
        else:
            df_plot['datetime'] = pd.to_datetime(df_plot['timestamp'])
    else:
        df_plot['datetime'] = df_plot.index

    quote = symbol.split('/')[1] if '/' in symbol else 'USDC'
    curr_price = float(df_plot['close'].iloc[-1]) if 'close' in df_plot.columns and not df_plot.empty else 0.0

    # 2-Row Subplot: Price + Volume (Top 75%), RSI (Bottom 25%)
    fig = make_subplots(
        rows=2, 
        cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.04, 
        row_heights=[0.75, 0.25],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
    )

    # 1. Candlestick Trace
    fig.add_trace(
        go.Candlestick(
            x=df_plot['datetime'],
            open=df_plot['open'],
            high=df_plot['high'],
            low=df_plot['low'],
            close=df_plot['close'],
            name=f"{symbol} Price",
            increasing_line_color='#0ECB81',  # Binance Green
            decreasing_line_color='#F6465D',  # Binance Red
            increasing_fillcolor='#0ECB81',
            decreasing_fillcolor='#F6465D',
            hoverlabel=dict(bgcolor='#1E222D')
        ),
        row=1, col=1, secondary_y=False
    )

    # 2. Bollinger Bands (if present)
    bbu_col = next((c for c in df_plot.columns if c.startswith('BBU')), None)
    bbl_col = next((c for c in df_plot.columns if c.startswith('BBL')), None)
    bbm_col = next((c for c in df_plot.columns if c.startswith('BBM')), None)

    if bbu_col and not df_plot[bbu_col].isna().all():
        fig.add_trace(
            go.Scatter(
                x=df_plot['datetime'],
                y=df_plot[bbu_col],
                name='BB Upper',
                line=dict(color='rgba(160, 175, 200, 0.35)', width=1, dash='dot'),
                hoverinfo='skip'
            ),
            row=1, col=1, secondary_y=False
        )

    if bbl_col and not df_plot[bbl_col].isna().all():
        fig.add_trace(
            go.Scatter(
                x=df_plot['datetime'],
                y=df_plot[bbl_col],
                name='BB Lower',
                line=dict(color='rgba(160, 175, 200, 0.35)', width=1, dash='dot'),
                fill='tonexty' if bbu_col else 'none',
                fillcolor='rgba(41, 98, 255, 0.04)',
                hoverinfo='skip'
            ),
            row=1, col=1, secondary_y=False
        )

    # 3. EMA Overlays
    if 'EMA_12' in df_plot.columns and not df_plot['EMA_12'].isna().all():
        fig.add_trace(
            go.Scatter(
                x=df_plot['datetime'],
                y=df_plot['EMA_12'],
                name='EMA 12 (Fast)',
                line=dict(color='#2962FF', width=1.8),
                hoverlabel=dict(bgcolor='#1E222D')
            ),
            row=1, col=1, secondary_y=False
        )

    if 'EMA_26' in df_plot.columns and not df_plot['EMA_26'].isna().all():
        fig.add_trace(
            go.Scatter(
                x=df_plot['datetime'],
                y=df_plot['EMA_26'],
                name='EMA 26 (Slow)',
                line=dict(color='#FF6D00', width=1.8),
                hoverlabel=dict(bgcolor='#1E222D')
            ),
            row=1, col=1, secondary_y=False
        )

    # 4. Volume Bars on secondary Y axis
    if 'volume' in df_plot.columns and not df_plot['volume'].isna().all():
        vol_colors = [
            'rgba(14, 203, 129, 0.35)' if c >= o else 'rgba(246, 70, 93, 0.35)'
            for c, o in zip(df_plot['close'], df_plot['open'])
        ]
        fig.add_trace(
            go.Bar(
                x=df_plot['datetime'],
                y=df_plot['volume'],
                name='Volume',
                marker_color=vol_colors,
                hoverinfo='skip'
            ),
            row=1, col=1, secondary_y=True
        )
        max_vol = df_plot['volume'].max()
        fig.update_yaxes(
            range=[0, max_vol * 4 if max_vol > 0 else 100], 
            showgrid=False, 
            showticklabels=False, 
            row=1, col=1, secondary_y=True
        )

    # 5. RSI Subplot (Row 2)
    if 'RSI_14' in df_plot.columns and not df_plot['RSI_14'].isna().all():
        fig.add_trace(
            go.Scatter(
                x=df_plot['datetime'],
                y=df_plot['RSI_14'],
                name='RSI (14)',
                line=dict(color='#AB47BC', width=1.8),
                hoverlabel=dict(bgcolor='#1E222D')
            ),
            row=2, col=1
        )
        
        # Overbought threshold (70)
        fig.add_hline(y=70, line_dash='dash', line_color='rgba(246, 70, 93, 0.6)', line_width=1, row=2, col=1)
        # Oversold threshold (30)
        fig.add_hline(y=30, line_dash='dash', line_color='rgba(14, 203, 129, 0.6)', line_width=1, row=2, col=1)
        # Midline (50)
        fig.add_hline(y=50, line_dash='dot', line_color='rgba(120, 120, 120, 0.4)', line_width=1, row=2, col=1)

        fig.update_yaxes(
            title_text="RSI",
            range=[10, 90],
            tickvals=[30, 50, 70],
            gridcolor='#2A2E39',
            row=2, col=1
        )

    # Header title with Regime tag
    regime_color = "#0ECB81" if regime == "TRENDING" else ("#F0B90B" if regime == "RANGING" else "#848E9C")
    title_text = (
        f"<b>{symbol}</b> ({timeframe}) &nbsp;|&nbsp; "
        f"<b>Last:</b> {curr_price:,.4f} {quote} &nbsp;|&nbsp; "
        f"<b>Regime:</b> <span style='color:{regime_color}'>{regime}</span>"
    )

    # Overall Dark Layout & Interactivity
    fig.update_layout(
        title=dict(text=title_text, font=dict(family="Inter, sans-serif", size=15, color="#EAECEF")),
        template='plotly_dark',
        plot_bgcolor='#131722',
        paper_bgcolor='#0e1117',
        xaxis_rangeslider_visible=False,
        hovermode='x unified',
        height=520,
        margin=dict(l=40, r=40, t=50, b=25),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10, color="#848E9C"),
            bgcolor='rgba(19, 23, 34, 0.7)'
        )
    )

    # X-axis crosshair / spikes
    fig.update_xaxes(
        gridcolor='#2A2E39',
        showspikes=True,
        spikemode='across',
        spikesnap='cursor',
        spikedash='solid',
        spikethickness=1,
        spikecolor='#555555'
    )
    fig.update_yaxes(
        gridcolor='#2A2E39',
        title_text=f"Price ({quote})",
        row=1, col=1, secondary_y=False
    )

    return fig

def create_equity_curve_chart(
    snapshots: List[Dict[str, Any]], 
    current_equity: float, 
    starting_capital: float = 30.0
) -> go.Figure:
    """
    Builds an interactive Equity Curve chart with high-water mark,
    5% max drawdown threshold, and $20 emergency circuit breaker floor.
    """
    if not snapshots:
        # Generate dummy single-point baseline
        times = [pd.Timestamp.now()]
        equities = [current_equity]
    else:
        times = [pd.to_datetime(s['timestamp']) for s in snapshots]
        equities = [float(s['equity']) for s in snapshots]
        # Append current equity as last point
        times.append(pd.Timestamp.now())
        equities.append(current_equity)

    hwm = max(equities) if equities else starting_capital
    max_dd_limit = hwm * 0.95 # 5% drawdown line
    emergency_floor = 20.0

    fig = go.Figure()

    # 1. Equity Area Line
    fig.add_trace(
        go.Scatter(
            x=times,
            y=equities,
            name="Portfolio Equity",
            mode="lines+markers",
            line=dict(color="#0ECB81", width=2.5),
            marker=dict(size=5, color="#0ECB81"),
            fill="tozeroy",
            fillcolor="rgba(14, 203, 129, 0.08)",
            hovertemplate="Time: %{x|%b %d %H:%M}<br>Equity: $%{y:.2f}<extra></extra>"
        )
    )

    # 2. High Water Mark Line
    fig.add_hline(
        y=hwm, 
        line_dash="dot", 
        line_color="rgba(41, 98, 255, 0.7)", 
        line_width=1.5,
        annotation_text=f"24h Peak (${hwm:.2f})",
        annotation_position="top right",
        annotation_font_size=10,
        annotation_font_color="#2962FF"
    )

    # 3. 5% Drawdown Boundary
    fig.add_hline(
        y=max_dd_limit, 
        line_dash="dash", 
        line_color="rgba(240, 185, 11, 0.7)", 
        line_width=1.5,
        annotation_text=f"5% Drawdown Trigger (${max_dd_limit:.2f})",
        annotation_position="bottom right",
        annotation_font_size=10,
        annotation_font_color="#F0B90B"
    )

    # 4. Emergency Capital Floor ($20.00)
    fig.add_hline(
        y=emergency_floor, 
        line_dash="dash", 
        line_color="rgba(246, 70, 93, 0.8)", 
        line_width=1.5,
        annotation_text="Emergency Floor ($20.00)",
        annotation_position="bottom left",
        annotation_font_size=10,
        annotation_font_color="#F6465D"
    )

    min_y = min(min(equities), 18.0)
    max_y = max(hwm * 1.05, 32.0)

    fig.update_layout(
        title=dict(
            text="<b>24h Rolling Equity & Risk Boundaries</b>", 
            font=dict(family="Inter, sans-serif", size=14, color="#EAECEF")
        ),
        template='plotly_dark',
        plot_bgcolor='#131722',
        paper_bgcolor='#0e1117',
        height=280,
        margin=dict(l=40, r=40, t=40, b=25),
        hovermode="x unified",
        yaxis=dict(
            title="Equity (USD)",
            range=[min_y, max_y],
            gridcolor="#2A2E39"
        ),
        xaxis=dict(
            gridcolor="#2A2E39"
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=10, color="#848E9C"),
            bgcolor='rgba(19, 23, 34, 0.7)'
        )
    )

    return fig

def get_tradingview_widget_html(symbol: str, timeframe: str = "60", height: int = 520) -> str:
    """
    Generates embed HTML for the official TradingView Advanced Real-Time Chart widget.
    Loads Binance streaming data directly in the browser with zero CCXT backend calls.
    """
    # Format Binance ticker, e.g. BTC/USDC -> BINANCE:BTCUSDC
    clean_sym = symbol.replace("/", "").replace("_", "")
    tv_symbol = f"BINANCE:{clean_sym}"
    
    # Map timeframe
    tf_map = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    interval = tf_map.get(timeframe, "60")

    return f"""
    <!-- TradingView Widget BEGIN -->
    <div class="tradingview-widget-container" style="height:{height}px;width:100%">
      <div id="tradingview_{clean_sym}" style="height:calc(100% - 32px);width:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{
        "autosize": true,
        "symbol": "{tv_symbol}",
        "interval": "{interval}",
        "timezone": "Etc/UTC",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "toolbar_bg": "#131722",
        "enable_publishing": false,
        "hide_side_toolbar": false,
        "allow_symbol_change": true,
        "details": true,
        "hotlist": false,
        "calendar": false,
        "studies": [
          "MASimple@tv-basicstudies",
          "RSI@tv-basicstudies"
        ],
        "container_id": "tradingview_{clean_sym}"
      }});
      </script>
    </div>
    <!-- TradingView Widget END -->
    """

def plot_market_graph(symbol: str, df: pd.DataFrame, timeframe: str):
    """
    Fallback static candlestick chart with EMA overlay saved to file.
    """
    try:
        os.makedirs("graphs", exist_ok=True)
        df_plot = df.copy()
        if 'timestamp' in df_plot.columns:
            df_plot['timestamp'] = pd.to_datetime(df_plot['timestamp'], unit='ms')
            df_plot.set_index('timestamp', inplace=True)
            
        safe_symbol = symbol.replace("/", "_")
        filename = f"graphs/{safe_symbol}_{timeframe}.png"
        
        addplots = []
        if 'EMA_12' in df_plot.columns and not df_plot['EMA_12'].isna().all():
            addplots.append(mpf.make_addplot(df_plot['EMA_12'], color='blue', width=1.5))
        if 'EMA_26' in df_plot.columns and not df_plot['EMA_26'].isna().all():
            addplots.append(mpf.make_addplot(df_plot['EMA_26'], color='orange', width=1.5))
            
        quote = symbol.split('/')[1] if '/' in symbol else 'USD'
        mpf.plot(
            df_plot, 
            type='candle', 
            style='charles', 
            addplot=addplots,
            title=f"{symbol} - {timeframe}",
            ylabel=f'Price ({quote})',
            savefig=filename,
            warn_too_much_data=1000
        )
        logger.info(f"Market graph saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to generate graph for {symbol}: {e}")

