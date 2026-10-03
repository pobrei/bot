import mplfinance as mpf
import pandas as pd
import logging
import os

logger = logging.getLogger(__name__)

def plot_market_graph(symbol: str, df: pd.DataFrame, timeframe: str):
    """
    Generates a candlestick chart with EMA overlay and saves it to a file.
    """
    try:
        # Create output dir if not exists
        os.makedirs("graphs", exist_ok=True)
        
        # We need a proper DatetimeIndex for mplfinance
        df_plot = df.copy()
        if 'timestamp' in df_plot.columns:
            df_plot['timestamp'] = pd.to_datetime(df_plot['timestamp'], unit='ms')
            df_plot.set_index('timestamp', inplace=True)
            
        safe_symbol = symbol.replace("/", "_")
        filename = f"graphs/{safe_symbol}_{timeframe}.png"
        
        # Prepare EMA plots
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
