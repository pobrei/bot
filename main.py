import os
import sys
import time
import socket
import atexit
import logging
import ccxt
from src.engine import TradingEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Main")

MAX_RESTART_ATTEMPTS = 5
RESTART_BASE_DELAY = 30
_SOCKET_LOCK = None

def acquire_instance_lock(port: int = 58999, lock_path: str = "trading_bot.pid"):
    """
    Prevents multiple concurrent bot daemon instances using an OS-kernel-enforced TCP socket.
    """
    global _SOCKET_LOCK
    if isinstance(port, str):
        lock_path = port
        port = 58999
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', port))
        s.listen(1)
        _SOCKET_LOCK = s

        try:
            with open(lock_path, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        def release():
            global _SOCKET_LOCK
            try:
                if _SOCKET_LOCK:
                    _SOCKET_LOCK.close()
                    _SOCKET_LOCK = None
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except Exception:
                pass

        atexit.register(release)
        logger.info(f"Acquired single-instance TCP socket lock on 127.0.0.1:{port} (PID: {os.getpid()})")
        return _SOCKET_LOCK
    except (OSError, socket.error) as e:
        logger.critical(f"FATAL: Port {port} already bound or another bot instance is running: {e}. Exiting.")
        return None

def main(attempt: int = 0):
    if attempt == 0:
        lock = acquire_instance_lock()
        if lock is None:
            logger.critical("Another bot instance is running. Exiting.")
            sys.exit(1)

    if attempt >= MAX_RESTART_ATTEMPTS:
        logger.critical(f"Bot failed {MAX_RESTART_ATTEMPTS} consecutive times. Halting permanently.")
        return

    try:
        engine = TradingEngine(skip_lock=True)
        engine.run()
    except (ccxt.NetworkError, ccxt.ExchangeError) as e:
        delay = RESTART_BASE_DELAY * (2 ** attempt)
        logger.error(f"Exchange/Network error (attempt {attempt+1}/{MAX_RESTART_ATTEMPTS}): {e}. Restarting in {delay}s...")
        time.sleep(delay)
        main(attempt + 1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Fatal unhandled error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
