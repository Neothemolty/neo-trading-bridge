from typing import Optional, Union, List
"""Persistent signal storage using SQLite. Survives restarts."""

import sqlite3
import threading
import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "signals.db"
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_db():
    """Create tables if they don't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL,
                received_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'accepted',
                order_id TEXT,
                error TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                order_id TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
            )
        """)


def signal_exists(signal_id: str) -> bool:
    """Check if a signal_id has already been processed."""
    with _lock:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            return row is not None


def store_signal(
    signal_id: str,
    strategy: str,
    symbol: str,
    action: str,
    price: Optional[float],
    status: str = "accepted",
    order_id: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    """Store a signal. Returns False if duplicate (already exists)."""
    with _lock:
        try:
            with _get_conn() as conn:
                conn.execute(
                    """INSERT INTO signals
                       (signal_id, strategy, symbol, action, price, received_at, status, order_id, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        signal_id,
                        strategy,
                        symbol,
                        action,
                        price,
                        datetime.datetime.utcnow().isoformat(),
                        status,
                        order_id,
                        error,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False  # Duplicate


def update_signal(signal_id: str, status: str, order_id: Optional[str] = None, error: Optional[str] = None):
    """Update a signal's status after execution."""
    with _lock:
        with _get_conn() as conn:
            conn.execute(
                "UPDATE signals SET status = ?, order_id = ?, error = ? WHERE signal_id = ?",
                (status, order_id, error, signal_id),
            )


def store_trade(signal_id: str, order_id: Optional[str], symbol: str, side: str, qty: Optional[str], status: str):
    """Log a trade."""
    with _lock:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO trades (signal_id, order_id, symbol, side, qty, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (signal_id, order_id, symbol, side, qty, status, datetime.datetime.utcnow().isoformat()),
            )


def recent_signals(limit: int = 20) -> List[dict]:
    """Get recent signals."""
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def recent_trades(limit: int = 20) -> List[dict]:
    """Get recent trades."""
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
