import sqlite3
import json
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "trading_bot.db"

def init_db():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            
            # Active positions table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    entry_price REAL,
                    highest_price REAL,
                    amount REAL,
                    quote TEXT,
                    trailing_active BOOLEAN,
                    entry_time TEXT
                )
            ''')
            
            # Trade history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    amount REAL,
                    quote TEXT,
                    pnl_pct REAL,
                    realized_pnl REAL,
                    reason TEXT
                )
            ''')
            
            # Equity snapshots table for drawdown tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    equity REAL
                )
            ''')
            
            # Bot state table for the Kill-Switch
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Decoupled portfolio state table for the Dashboard
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS portfolio_state (
                    id INTEGER PRIMARY KEY,
                    total_equity REAL,
                    balances_json TEXT
                )
            ''')

            # Cached market candles and indicators for interactive charting
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS market_candles (
                    symbol TEXT,
                    timeframe TEXT,
                    data_json TEXT,
                    regime TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (symbol, timeframe)
                )
            ''')
            
            # Initialize kill_switch if it doesn't exist
            cursor.execute('''
                INSERT OR IGNORE INTO bot_state (key, value) VALUES ('kill_switch', 'false')
            ''')
            
            conn.commit()
    except Exception as e:
        logger.error(f"Error initializing database: {e}")

def get_kill_switch() -> bool:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = 'kill_switch'")
            row = cursor.fetchone()
            if row:
                return row[0].lower() == 'true'
            return False
    except Exception as e:
        logger.error(f"Error reading kill_switch: {e}")
        return False

def set_kill_switch(is_killed: bool):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            val = 'true' if is_killed else 'false'
            cursor.execute("UPDATE bot_state SET value = ? WHERE key = 'kill_switch'", (val,))
            conn.commit()
    except Exception as e:
        logger.error(f"Error setting kill_switch: {e}")

def get_open_positions() -> Dict[str, Any]:
    positions = {}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            for row in cursor.fetchall():
                positions[row['symbol']] = {
                    "symbol": row['symbol'],
                    "entry_price": row['entry_price'],
                    "highest_price": row['highest_price'],
                    "amount": row['amount'],
                    "quote": row['quote'],
                    "trailing_active": bool(row['trailing_active']),
                    "entry_time": row['entry_time']
                }
    except Exception as e:
        logger.error(f"Error reading positions from DB: {e}")
    return positions

def get_open_position(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE symbol = ?", (symbol,))
            row = cursor.fetchone()
            if row:
                return {
                    "symbol": row['symbol'],
                    "entry_price": row['entry_price'],
                    "highest_price": row['highest_price'],
                    "amount": row['amount'],
                    "quote": row['quote'],
                    "trailing_active": bool(row['trailing_active']),
                    "entry_time": row['entry_time']
                }
    except Exception as e:
        logger.error(f"Error reading position {symbol} from DB: {e}")
    return None

def upsert_position(pos: Dict[str, Any]):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO positions (symbol, entry_price, highest_price, amount, quote, trailing_active, entry_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    highest_price=excluded.highest_price,
                    trailing_active=excluded.trailing_active
            ''', (
                pos['symbol'], pos['entry_price'], pos['highest_price'], 
                pos['amount'], pos['quote'], pos['trailing_active'], pos['entry_time']
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"Error upserting position {pos.get('symbol')} to DB: {e}")

def remove_position(symbol: str):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            conn.commit()
    except Exception as e:
        logger.error(f"Error removing position {symbol} from DB: {e}")

def get_trade_history() -> List[Dict[str, Any]]:
    history = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_history ORDER BY id ASC")
            for row in cursor.fetchall():
                history.append(dict(row))
    except Exception as e:
        logger.error(f"Error reading trade history from DB: {e}")
    return history

def insert_trade_history(trade: Dict[str, Any]):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trade_history (symbol, entry_time, exit_time, entry_price, exit_price, amount, quote, pnl_pct, realized_pnl, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade['symbol'], trade['entry_time'], trade['exit_time'],
                trade['entry_price'], trade['exit_price'], trade['amount'],
                trade['quote'], trade['pnl_pct'], trade['realized_pnl'], trade['reason']
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"Error inserting trade history into DB: {e}")

def record_equity_snapshot(equity: float):
    now_str = datetime.now().isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO equity_snapshots (timestamp, equity)
                VALUES (?, ?)
            ''', (now_str, equity))
            conn.commit()
    except Exception as e:
        logger.error(f"Error recording equity snapshot: {e}")

def get_equity_snapshots(hours: int = 24) -> List[Dict[str, Any]]:
    snapshots = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Simple approach: fetch all and filter in Python, or use datetime in SQLite
            cursor.execute("SELECT * FROM equity_snapshots ORDER BY timestamp ASC")
            now = datetime.now()
            for row in cursor.fetchall():
                try:
                    ts = datetime.fromisoformat(row['timestamp'])
                    if (now - ts).total_seconds() <= hours * 3600:
                        snapshots.append(dict(row))
                except:
                    pass
    except Exception as e:
        logger.error(f"Error reading equity snapshots: {e}")
    return snapshots

def save_portfolio_state(total_equity: float, balances: Dict[str, Any]):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO portfolio_state (id, total_equity, balances_json)
                VALUES (1, ?, ?)
            ''', (total_equity, json.dumps(balances)))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving portfolio state: {e}")

def get_portfolio_state() -> Tuple[float, Dict[str, Any]]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT total_equity, balances_json FROM portfolio_state WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return float(row['total_equity']), json.loads(row['balances_json'])
    except Exception as e:
        logger.error(f"Error reading portfolio state: {e}")
    # Return defaults if nothing found
    return 30.0, {}

def save_market_candles(symbol: str, timeframe: str, df: Any, regime: str = "UNKNOWN"):
    """
    Saves the latest OHLCV market candles and computed indicators to SQLite.
    Decouples UI rendering completely from CCXT rate limits.
    """
    try:
        import io
        import pandas as pd
        if not isinstance(df, pd.DataFrame) or df.empty:
            return
        
        # Take the most recent 120 candles to keep DB footprint minimal (<50KB)
        df_slice = df.tail(120).copy()
        
        # Keep only relevant market & indicator columns
        cols = [c for c in df_slice.columns if c in [
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'EMA_12', 'EMA_26', 'RSI_14'
        ] or any(c.startswith(p) for p in ['BBL', 'BBM', 'BBU', 'MACD', 'ADX', 'DMP', 'DMN'])]
        
        df_save = df_slice[cols] if cols else df_slice
        data_json = df_save.to_json(orient='split')
        now_str = datetime.now().isoformat()
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO market_candles (symbol, timeframe, data_json, regime, updated_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (symbol, timeframe, data_json, regime, now_str))
            conn.commit()
    except Exception as e:
        logger.error(f"Error caching market candles for {symbol}: {e}")

def get_market_candles(symbol: str, timeframe: str = "1h") -> Optional[Tuple[Any, str, str]]:
    """
    Retrieves cached OHLCV market candles and computed indicators from SQLite.
    Returns (df, regime, updated_at) or None.
    """
    try:
        import io
        import pandas as pd
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT data_json, regime, updated_at FROM market_candles WHERE symbol = ? AND timeframe = ?", 
                (symbol, timeframe)
            )
            row = cursor.fetchone()
            if row and row['data_json']:
                df = pd.read_json(io.StringIO(row['data_json']), orient='split')
                return df, row['regime'] or "UNKNOWN", row['updated_at']
    except Exception as e:
        logger.error(f"Error reading cached candles for {symbol}: {e}")
    return None

def get_cached_market_symbols() -> List[str]:
    """
    Returns list of all symbols currently cached in the database.
    """
    symbols = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT symbol FROM market_candles ORDER BY symbol ASC")
            symbols = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching cached symbols: {e}")
    return symbols

# Initialize DB on load
init_db()
