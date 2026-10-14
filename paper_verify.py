import argparse
import os
import sys
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

import ccxt
from src.paper_verification import EventDrivenPaperEngine
from src import database

def fetch_data(symbol: str, timeframe: str = "15m", limit: int = 500) -> pd.DataFrame:
    """Fetches market data from Binance CCXT or falls back to SQLite cache."""
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        print(f"📡 Fetching {limit} {timeframe} candles for {symbol} from Binance...")
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if ohlcv:
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ Fetched {len(df)} candles successfully.")
            return df
    except Exception as e:
        print(f"⚠️ CCXT fetch failed ({e}). Checking local SQLite cache...")

    cached = database.get_market_candles(symbol, timeframe)
    if cached is not None:
        df, regime, updated = cached
        print(f"✅ Loaded {len(df)} cached candles from SQLite (Last updated: {updated}).")
        return df

    raise RuntimeError(f"Unable to load market data for {symbol} ({timeframe}).")

def main():
    parser = argparse.ArgumentParser(description="Rigorous Event-Driven Paper Trading Verification Engine")
    parser.add_argument("--symbol", type=str, default="BTC/USDC", help="Symbol to verify (default: BTC/USDC)")
    parser.add_argument("--timeframe", type=str, default="15m", help="Timeframe (default: 15m)")
    parser.add_argument("--limit", type=int, default=500, help="Number of historical candles (default: 500)")
    parser.add_argument("--capital", type=float, default=30.0, help="Starting capital in USD (default: 30.0)")
    parser.add_argument("--split", type=float, default=0.70, help="In-Sample Walk-Forward Split ratio (default: 0.70)")
    parser.add_argument("--min-atr", type=float, default=None, help="Minimum ATR / Close ratio (default: auto-scaled to timeframe)")
    args = parser.parse_args()

    print("=" * 70)
    print("🔬 QUANTITATIVE EVENT-DRIVEN PAPER TRADING VERIFICATION PROTOCOL")
    print("=" * 70)
    print(f"Symbol:           {args.symbol}")
    print(f"Timeframe:        {args.timeframe}")
    print(f"Starting Capital: ${args.capital:.2f}")
    print(f"Walk-Forward:     {int(args.split*100)}% In-Sample / {int((1-args.split)*100)}% Out-of-Sample")
    print(f"Friction Model:   0.075% Comm (BNB Tier) | 0.05% Slippage | 0.05% Spread")
    print("=" * 70)

    df = fetch_data(args.symbol, args.timeframe, args.limit)
    engine = EventDrivenPaperEngine(
        starting_capital=args.capital,
        timeframe=args.timeframe,
        min_atr_ratio=args.min_atr
    )

    results = engine.run_simulation(df, symbol=args.symbol, walk_forward_split=args.split)
    summary = results['summary']
    in_sample = results['in_sample']
    out_of_sample = results['out_of_sample']
    trades = results['trade_log']

    print("\n📊 OVERALL PERFORMANCE SUMMARY:")
    print("-" * 50)
    print(f"  Total Trades:         {summary['total_trades']} ({summary['win_trades']} Wins / {summary['loss_trades']} Losses)")
    print(f"  Win Rate:             {summary['win_rate_pct']:.1f}%")
    print(f"  Net Realized PnL:     ${summary['net_pnl']:+.4f}")
    print(f"  Return on Capital:    {summary['return_pct']:+.2f}%")
    print(f"  Profit Factor:        {summary['profit_factor']:.2f}")
    print(f"  Max Drawdown:         {summary['max_drawdown_pct']:.2f}%")
    print(f"  Annualized Sharpe:    {summary['sharpe_ratio']:.2f}")
    psr_val = summary['psr']
    psr_status = "🟢 PASS (Edge Validated)" if psr_val >= 0.90 else ("🟡 MODERATE" if psr_val >= 0.70 else "🔴 LOW (Insufficient Sample/Edge)")
    print(f"  Probabilistic Sharpe: {psr_val:.4f} -> {psr_status}")
    print("-" * 50)

    print("\n🧪 WALK-FORWARD ANALYSIS (OOS VALIDATION):")
    print(f"{'Metric':<22} | {'In-Sample (70%)':<18} | {'Out-of-Sample (30%)':<18}")
    print("-" * 64)
    print(f"{'Trades':<22} | {in_sample['total_trades']:<18} | {out_of_sample['total_trades']:<18}")
    print(f"{'Win Rate':<22} | {in_sample['win_rate_pct']:<17.1f}% | {out_of_sample['win_rate_pct']:<17.1f}%")
    print(f"{'Return %':<22} | {in_sample['return_pct']:<17.2f}% | {out_of_sample['return_pct']:<17.2f}%")
    print(f"{'Profit Factor':<22} | {in_sample['profit_factor']:<18.2f} | {out_of_sample['profit_factor']:<18.2f}")
    print(f"{'Max Drawdown':<22} | {in_sample['max_drawdown_pct']:<17.2f}% | {out_of_sample['max_drawdown_pct']:<17.2f}%")
    print("-" * 64)

    if trades:
        print(f"\n📜 RECENT EXECUTED SIMULATION TRADES (Last {min(5, len(trades))}):")
        df_trades = pd.DataFrame(trades)[['symbol', 'entry_time', 'exit_time', 'entry_price', 'exit_price', 'pnl_pct', 'realized_pnl', 'reason', 'segment']]
        print(df_trades.tail(5).to_string(index=False))

    # Save Markdown report
    os.makedirs("reports", exist_ok=True)
    report_file = f"reports/paper_verification_{args.symbol.replace('/', '_')}_{args.timeframe}.md"
    with open(report_file, "w") as f:
        f.write(f"# 🔬 Quantitative Paper Trading Verification: {args.symbol} ({args.timeframe})\n\n")
        f.write(f"- **Execution Timestamp:** {datetime.now().isoformat()}\n")
        f.write(f"- **Starting Capital:** ${args.capital:.2f}\n")
        f.write(f"- **Total Bars Analyzed:** {len(df)}\n")
        f.write(f"- **Probabilistic Sharpe Ratio (PSR):** {summary['psr']:.4f}\n\n")
        f.write("## Overall Metrics\n\n")
        f.write(f"| Metric | Value |\n| :--- | :--- |\n")
        f.write(f"| Net Realized PnL | ${summary['net_pnl']:+.4f} |\n")
        f.write(f"| Return on Capital | {summary['return_pct']:+.2f}% |\n")
        f.write(f"| Total Trades | {summary['total_trades']} |\n")
        f.write(f"| Win Rate | {summary['win_rate_pct']:.1f}% |\n")
        f.write(f"| Profit Factor | {summary['profit_factor']:.2f} |\n")
        f.write(f"| Max Drawdown | {summary['max_drawdown_pct']:.2f}% |\n")
        f.write(f"| Annualized Sharpe Ratio | {summary['sharpe_ratio']:.2f} |\n")
        f.write(f"| PSR Score | {summary['psr']:.4f} |\n\n")
        f.write("## Walk-Forward Analysis (In-Sample vs Out-of-Sample)\n\n")
        f.write(f"| Metric | In-Sample (70%) | Out-of-Sample (30%) |\n| :--- | :--- | :--- |\n")
        f.write(f"| Total Trades | {in_sample['total_trades']} | {out_of_sample['total_trades']} |\n")
        f.write(f"| Win Rate | {in_sample['win_rate_pct']:.1f}% | {out_of_sample['win_rate_pct']:.1f}% |\n")
        f.write(f"| Return % | {in_sample['return_pct']:+.2f}% | {out_of_sample['return_pct']:+.2f}% |\n")
        f.write(f"| Profit Factor | {in_sample['profit_factor']:.2f} | {out_of_sample['profit_factor']:.2f} |\n")
        f.write(f"| Max Drawdown | {in_sample['max_drawdown_pct']:.2f}% | {out_of_sample['max_drawdown_pct']:.2f}% |\n")
    print(f"\n💾 Report saved to: {report_file}")

if __name__ == "__main__":
    main()
