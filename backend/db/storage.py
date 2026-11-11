import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("MaguraBot.Storage")

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DB_DIR / "magurabot.db"


class StorageService:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._init_db_sync()

    def _init_db_sync(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Closed Trades Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    token_mint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    mode TEXT NOT NULL,
                    buy_price_sol REAL,
                    buy_price_usd REAL,
                    sell_price_sol REAL,
                    sol_invested REAL,
                    tokens_held REAL,
                    pnl_sol REAL,
                    pnl_usd REAL,
                    pnl_pct REAL,
                    exit_reason TEXT,
                    opened_at REAL,
                    closed_at REAL
                )
            """)

            # Orders Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    token_mint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    amount_sol REAL,
                    amount_tokens REAL,
                    price_sol REAL,
                    tx_hash TEXT,
                    created_at REAL,
                    completed_at REAL,
                    error_message TEXT
                )
            """)

            # Discovered Tokens Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovered_tokens (
                    mint TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    source TEXT,
                    safety_score INTEGER,
                    discovered_at REAL
                )
            """)
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    def _save_trade_sync(self, trade_data: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trades (
                    id, token_mint, symbol, name, mode, buy_price_sol, buy_price_usd,
                    sell_price_sol, sol_invested, tokens_held, pnl_sol, pnl_usd,
                    pnl_pct, exit_reason, opened_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get("id"),
                trade_data.get("token_mint"),
                trade_data.get("symbol"),
                trade_data.get("name"),
                trade_data.get("mode"),
                trade_data.get("buy_price_sol"),
                trade_data.get("buy_price_usd"),
                trade_data.get("current_price_sol"),
                trade_data.get("sol_invested"),
                trade_data.get("tokens_held"),
                trade_data.get("pnl_sol"),
                trade_data.get("pnl_usd"),
                trade_data.get("pnl_pct"),
                str(trade_data.get("exit_reason") or "manual"),
                trade_data.get("opened_at"),
                trade_data.get("closed_at"),
            ))
            conn.commit()

    async def save_trade(self, trade_data: Dict[str, Any]):
        await asyncio.to_thread(self._save_trade_sync, trade_data)

    def _get_recent_trades_sync(self, limit: int = 100) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_recent_trades_sync, limit)

    def _save_order_sync(self, order_data: Dict[str, Any]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orders (
                    id, token_mint, symbol, order_type, status, mode,
                    amount_sol, amount_tokens, price_sol, tx_hash,
                    created_at, completed_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_data.get("id"),
                order_data.get("token_mint"),
                order_data.get("symbol"),
                str(order_data.get("order_type")),
                str(order_data.get("status")),
                str(order_data.get("mode")),
                order_data.get("amount_sol"),
                order_data.get("amount_tokens"),
                order_data.get("price_sol"),
                order_data.get("tx_hash"),
                order_data.get("created_at"),
                order_data.get("completed_at"),
                order_data.get("error_message"),
            ))
            conn.commit()

    async def save_order(self, order_data: Dict[str, Any]):
        await asyncio.to_thread(self._save_order_sync, order_data)

    def _get_trade_stats_sync(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*), 
                    COALESCE(SUM(pnl_sol), 0.0), 
                    COALESCE(SUM(CASE WHEN pnl_sol > 0 THEN 1 ELSE 0 END), 0)
                FROM trades
            """)
            row = cursor.fetchone()
            total_trades = row[0] if row else 0
            total_pnl = float(row[1]) if row else 0.0
            wins = row[2] if row else 0
            losses = max(0, total_trades - wins)
            win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
            return {
                "total_trades": total_trades,
                "total_pnl_sol": total_pnl,
                "wins": wins,
                "losses": losses,
                "win_rate_pct": win_rate,
            }

    async def get_trade_stats(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self._get_trade_stats_sync)
