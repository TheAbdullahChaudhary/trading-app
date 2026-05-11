"""Data fetcher: loads historical OHLCV and maintains live candle cache."""
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from bot.logger import get_logger
from bot.mexc_client import MEXCClient

log = get_logger("data_fetcher")


class DataFetcher:
    def __init__(self, client: MEXCClient, symbols: List[str],
                 interval: str = "Min15", lookback: int = 500,
                 db_path: str = "data/trades.db"):
        self.client = client
        self.symbols = symbols
        self.interval = interval
        self.lookback = lookback
        self.db_path = db_path
        self._lock = threading.Lock()
        self._candles: Dict[str, pd.DataFrame] = {}
        self._latest_price: Dict[str, float] = {}
        self._init_db()

    # ------------------- DB -------------------

    def _init_db(self):
        import os
        os.makedirs("data", exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT,
                    time INTEGER,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    PRIMARY KEY (symbol, time)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    volume REAL,
                    pnl REAL,
                    opened_at TEXT,
                    closed_at TEXT,
                    status TEXT DEFAULT 'open',
                    order_id TEXT,
                    stop_loss REAL,
                    take_profit REAL,
                    signal TEXT,
                    confidence REAL
                )
            """)
            conn.commit()
        log.info(f"Database initialized: {self.db_path}")

    def _save_candles(self, symbol: str, candles: List[dict]):
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?)",
                [(symbol, c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"])
                 for c in candles]
            )
            conn.commit()

    # ------------------- LOAD -----------------

    def load_history(self):
        """Fetch historical candles for all symbols on startup."""
        for sym in self.symbols:
            log.info(f"Loading history for {sym}...")
            candles = self.client.get_klines(sym, self.interval, self.lookback)
            if candles:
                self._save_candles(sym, candles)
                df = pd.DataFrame(candles)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df.set_index("time", inplace=True)
                df.sort_index(inplace=True)
                with self._lock:
                    self._candles[sym] = df
                log.info(f"  [OK] {sym}: {len(df)} candles loaded")
            else:
                log.warning(f"  [FAIL] No candles for {sym}")

    # ------------------- LIVE -----------------

    def on_kline(self, data: dict):
        """Called by WebSocket on new kline tick."""
        try:
            symbol = data.get("symbol", "")
            d = data.get("data", {})
            # MEXC WS kline data can arrive as a list or dict
            if isinstance(d, list):
                d = d[0] if d else {}
            if not isinstance(d, dict):
                return
            candle = {
                "time": int(d.get("t", d.get("time", 0))),
                "open":   float(d.get("o", d.get("open",  0))),
                "high":   float(d.get("h", d.get("high",  0))),
                "low":    float(d.get("l", d.get("low",   0))),
                "close":  float(d.get("c", d.get("close", 0))),
                "volume": float(d.get("a", d.get("vol",   d.get("volume", 0)))),
            }
            if candle["close"] == 0:
                return
            self._save_candles(symbol, [candle])
            t = pd.to_datetime(candle["time"], unit="s")
            row = pd.DataFrame([{k: v for k, v in candle.items() if k != "time"}], index=[t])
            with self._lock:
                if symbol in self._candles:
                    df = self._candles[symbol]
                    if t in df.index:
                        df.loc[t] = row.iloc[0]
                    else:
                        self._candles[symbol] = pd.concat([df, row]).tail(self.lookback)
                else:
                    self._candles[symbol] = row
            log.debug(f"Kline update: {symbol} close={candle['close']}")
        except Exception as e:
            log.debug(f"on_kline parse: {e} | raw={str(data)[:120]}")

    def on_ticker(self, data: dict):
        """Update latest price from ticker."""
        try:
            symbol = data.get("symbol", "")
            d = data.get("data", {})
            # Handle list wrapper
            if isinstance(d, list):
                d = d[0] if d else {}
            price = float(d.get("lastPrice", d.get("last", d.get("p", 0))))
            if price > 0:
                with self._lock:
                    self._latest_price[symbol] = price
        except Exception as e:
            log.debug(f"on_ticker parse: {e}")

    def get_df(self, symbol: str) -> Optional[pd.DataFrame]:
        with self._lock:
            return self._candles.get(symbol, None)

    def get_price(self, symbol: str) -> float:
        with self._lock:
            return self._latest_price.get(symbol, 0.0)

    def get_all_prices(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._latest_price)
