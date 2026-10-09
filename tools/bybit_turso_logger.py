import asyncio
import os

import libsql_client
from dotenv import load_dotenv

# Load from global .env if needed, or local
load_dotenv()


async def log_to_turso(event_type, data):
    """
    Logs an event to the Turso cloud database.
    event_type: 'TRADE', 'ORDER_CANCEL', 'ERROR', 'BOT_START'
    data: Dictionary containing event details
    """
    url = os.getenv("TURSO_DATABASE_URL")
    if url and url.startswith("libsql://"):
        url = url.replace("libsql://", "https://")

    token = os.getenv("TURSO_AUTH_TOKEN")

    if not url:
        return  # Skip if not configured

    try:
        async with libsql_client.create_client(url=url, auth_token=token) as client:
            # Ensure tables exist
            await client.execute("""
                CREATE TABLE IF NOT EXISTS tool_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,
                    symbol TEXT,
                    side TEXT,
                    price REAL,
                    qty REAL,
                    details TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            symbol = data.get("symbol", "N/A")
            side = data.get("side", "N/A")
            price = data.get("price", 0.0)
            qty = data.get("qty", 0.0)
            details = str(data.get("details", ""))

            await client.execute(
                "INSERT INTO tool_logs (event_type, symbol, side, price, qty, details) VALUES (?, ?, ?, ?, ?, ?)",
                (event_type, symbol, side, price, qty, details),
            )
    except Exception as e:
        # We don't want to crash the main tool if logging fails
        print(f"[Turso Logger Error] {e}")


def log_event(event_type, data):
    """Synchronous wrapper for async logging"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(log_to_turso(event_type, data))
        else:
            loop.run_until_complete(log_to_turso(event_type, data))
    except Exception:
        # Fallback for environments where loop is tricky
        asyncio.run(log_to_turso(event_type, data))
