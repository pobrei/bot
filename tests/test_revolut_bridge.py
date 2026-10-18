import pytest
from unittest.mock import MagicMock, patch
from src.revolut_bridge import RevolutSignalBridge

def test_clean_base_symbol():
    assert RevolutSignalBridge.clean_base_symbol("BTC-USD") == "BTC"
    assert RevolutSignalBridge.clean_base_symbol("STX4847-USD") == "STX"
    assert RevolutSignalBridge.clean_base_symbol("SUI20947-USD") == "SUI"
    assert RevolutSignalBridge.clean_base_symbol("ARB11841-USD") == "ARB"
    assert RevolutSignalBridge.clean_base_symbol("TON11419-USD") == "TON"
    assert RevolutSignalBridge.clean_base_symbol("SOL/USDT") == "SOL"
    assert RevolutSignalBridge.clean_base_symbol("") == ""

def test_map_to_binance_without_exchange():
    bridge = RevolutSignalBridge(exchange=None)
    assert bridge.map_to_binance("SOL-USD", "USDC") == "SOL/USDC"
    assert bridge.map_to_binance("STX4847-USD", "USDC") == "STX/USDC"
    assert bridge.map_to_binance("USDC-USD", "USDC") is None
    assert bridge.map_to_binance("USDT-USD", "USDC") is None

def test_map_to_binance_with_exchange():
    mock_exchange = MagicMock()
    mock_exchange.markets = {
        "SOL/USDC": {"spot": True},
        "MANA/USDT": {"spot": True},
        "BTC/USDC": {"spot": True}
    }
    bridge = RevolutSignalBridge(exchange=mock_exchange)
    
    # Preferred quote is USDC
    assert bridge.map_to_binance("SOL-USD", "USDC") == "SOL/USDC"
    # Fallback to USDT if USDC not available
    assert bridge.map_to_binance("MANA-USD", "USDC") == "MANA/USDT"
    # Stablecoin base must be rejected
    assert bridge.map_to_binance("USDC-USD", "USDC") is None
    # Unknown base symbol
    assert bridge.map_to_binance("NONEXISTENT-USD", "USDC") is None

def test_get_candidates_and_intel():
    import time
    bridge = RevolutSignalBridge(exchange=None)
    now = time.time()
    fake_snapshots = [
        ["STX4847-USD", 0.40, "BUY", 0.70, 1.0, 0.0, "Bullish technical setup", now],
        ["SOL-USD", 120.0, "SELL", -0.55, -0.7, -0.2, "Bearish news dump", now],
        ["AAPL", 220.0, "BUY", 0.85, 0.8, 0.9, "Stock to skip", now],
        ["SAND-USD", 0.08, "BUY", 0.20, 0.3, 0.1, "Weak buy below threshold", now],
        ["STALE-USD", 1.00, "BUY", 0.90, 1.0, 0.0, "Stale setup older than 12h", now - 50000.0]
    ]

    with patch.object(bridge, "fetch_snapshots", return_value=fake_snapshots):
        intel = bridge.get_all_intel(preferred_quote="USDC", force_refresh=True)
        assert "STX/USDC" in intel
        assert "SOL/USDC" in intel
        assert "AAPL" not in intel # Non-crypto skipped
        
        candidates = bridge.get_candidates(min_score=0.35, preferred_quote="USDC")
        # STALE-USD must be excluded due to > 1h age
        assert len(candidates) == 1
        assert candidates[0]["binance_symbol"] == "STX/USDC"
        assert candidates[0]["composite_score"] == 0.70

        # Sentiment lookup
        sol_sent = bridge.get_sentiment("SOL/USDC")
        assert sol_sent is not None
        assert sol_sent["action"] == "SELL"
        assert sol_sent["composite_score"] == -0.55

        # Emergency sell check
        is_emerg, reason = bridge.is_emergency_sell("SOL/USDC", sell_threshold=-0.50)
        assert is_emerg is True
        assert "Revolut AI Emergency SELL" in reason

        # Candidate below threshold not emergency
        is_emerg_stx, _ = bridge.is_emergency_sell("STX/USDC", sell_threshold=-0.50)
        assert is_emerg_stx is False

