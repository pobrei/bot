import os
import pytest
from src import database

@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path):
    """
    Ensures every test runs against an isolated, temporary SQLite database.
    Prevents tests from polluting the production trading_bot.db.
    """
    test_db = str(tmp_path / "test_trading_bot.db")
    database.set_db_path(test_db)
    database.init_db()
    yield
    # Restore default path
    database.set_db_path("trading_bot.db")
