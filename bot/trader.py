"""Order execution and position tracking engine."""
import sqlite3
import threading
import time
from datetime import datetime
from typing import List, Optional

from bot.logger       import get_logger
from bot.mexc_client  import MEXCClient
from bot.risk_manager import RiskManager
from bot.strategy     import TradeSignal

log = get_logger("trader")

# MEXC perpetual contract sizes (USDT-margined)
# PnL = price_diff × volume × contract_size
CONTRACT_SIZES = {
    "BTC_USDT":   0.0001,
    "ETH_USDT":   0.01,
    "XAUT_USDT":  0.001,
    "SILVER_USDT":0.01,
    "USOIL_USDT": 0.01,
}
DEFAULT_CONTRACT_SIZE = 1.0



class Position:
    def __init__(self, symbol, side, volume, entry_price, stop_loss,
                 take_profit, order_id, signal, confidence, db_id=None,
                 sl_dist=0.0):
        self.symbol         = symbol
        self.side           = side        # "BUY" or "SELL"
        self.volume         = volume
        self.entry_price    = entry_price
        self.stop_loss      = stop_loss
        self.take_profit    = take_profit
        self.order_id       = order_id
        self.signal         = signal
        self.confidence     = confidence
        self.opened_at      = datetime.utcnow().isoformat()
        self.db_id          = db_id
        self.unrealized_pnl = 0.0
        self.sl_dist        = sl_dist     # ATR-based SL distance for breakeven
        self.breakeven_set  = False       # flag so we only move SL once

    @property
    def close_side(self) -> int:
        """MEXC side for closing: 4=Close Long, 2=Close Short"""
        return 4 if self.side == "BUY" else 2

    def update_pnl(self, current_price: float):
        cs = CONTRACT_SIZES.get(self.symbol, DEFAULT_CONTRACT_SIZE)
        if self.side == "BUY":
            self.unrealized_pnl = (current_price - self.entry_price) * self.volume * cs
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.volume * cs



class Trader:
    def __init__(self, client: MEXCClient, risk: RiskManager,
                 db_path: str = "data/trades.db", dry_run: bool = False):
        self.client   = client
        self.risk     = risk
        self.db_path  = db_path
        self.dry_run  = dry_run
        self._lock    = threading.Lock()
        self._positions: List[Position] = {}  # symbol -> Position
        self._trade_log: List[dict] = []
        self._event_callbacks = []
        self._load_open_positions()
        log.info(f"Trader ready | dry_run={dry_run} | open={len(self._positions)}")

    def add_event_callback(self, fn):
        self._event_callbacks.append(fn)

    def _emit(self, event: str, data: dict):
        for fn in self._event_callbacks:
            try:
                fn(event, data)
            except Exception:
                pass

    # ------------------- DB -------------------

    def _load_open_positions(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT id,symbol,side,volume,entry_price,stop_loss,take_profit,order_id,signal,confidence "
                    "FROM trades WHERE status='open'"
                ).fetchall()
            for row in rows:
                p = Position(*row[1:], db_id=row[0])
                self._positions[row[1]] = p
            log.info(f"Loaded {len(self._positions)} open positions from DB")
        except Exception as e:
            log.error(f"Error loading positions: {e}")
            self._positions = {}

    def _save_trade_open(self, pos: Position) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO trades (symbol,side,entry_price,volume,stop_loss,take_profit,"
                "opened_at,status,order_id,signal,confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pos.symbol, pos.side, pos.entry_price, pos.volume,
                 pos.stop_loss, pos.take_profit, pos.opened_at,
                 "open", pos.order_id, pos.signal, pos.confidence)
            )
            conn.commit()
            return cur.lastrowid

    def _save_trade_close(self, pos: Position, exit_price: float, pnl: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE trades SET exit_price=?,pnl=?,closed_at=?,status='closed' WHERE id=?",
                (exit_price, pnl, datetime.utcnow().isoformat(), pos.db_id)
            )
            conn.commit()

    # ------------------- OPEN -----------------

    def open_position(self, sig: TradeSignal, order_info: dict) -> bool:
        symbol = sig.symbol
        with self._lock:
            if symbol in self._positions:
                log.info(f"Already have position in {symbol}, skipping")
                return False

        vol  = order_info["volume"]
        sl   = order_info["stop_loss"]
        tp   = order_info["take_profit"]
        side = order_info["side"]

        result = self.client.place_order(
            symbol=symbol,
            side=side,
            vol=vol,
            stop_loss_price=sl,
            take_profit_price=tp,
        )

        order_id = str(result.get("data", "DRY_" + str(int(time.time()))))

        pos = Position(
            symbol=symbol,
            side=sig.signal,
            volume=vol,
            entry_price=sig.price,
            stop_loss=sl,
            take_profit=tp,
            order_id=order_id,
            signal=sig.signal,
            confidence=sig.confidence,
            sl_dist=order_info.get("sl_dist", 0.0),
        )
        pos.db_id = self._save_trade_open(pos)

        with self._lock:
            self._positions[symbol] = pos

        log.info(f"[OPEN] {sig.signal} {symbol} | entry={sig.price} vol={vol} SL={sl} TP={tp}")
        self._emit("trade_opened", {
            "symbol": symbol, "side": sig.signal,
            "entry": sig.price, "sl": sl, "tp": tp,
            "volume": vol, "confidence": sig.confidence,
            "order_id": order_id, "time": datetime.utcnow().isoformat()
        })
        return True

    # ------------------- MONITOR --------------

    def monitor_positions(self, prices: dict):
        """Check SL/TP for all open positions, also applies breakeven stop."""
        with self._lock:
            symbols = list(self._positions.keys())

        for sym in symbols:
            price = prices.get(sym, 0)
            if price <= 0:
                continue

            with self._lock:
                pos = self._positions.get(sym)
            if not pos:
                continue

            pos.update_pnl(price)

            # -- Breakeven stop: once 1xsl_dist in profit, move SL to entry --
            if not pos.breakeven_set and pos.sl_dist > 0:
                if pos.side == "BUY" and price >= pos.entry_price + pos.sl_dist:
                    pos.stop_loss  = round(pos.entry_price + pos.sl_dist * 0.1, 6)
                    pos.breakeven_set = True
                    log.info(f"[BE] Breakeven set {sym}: SL->{pos.stop_loss:.4f}")
                elif pos.side == "SELL" and price <= pos.entry_price - pos.sl_dist:
                    pos.stop_loss  = round(pos.entry_price - pos.sl_dist * 0.1, 6)
                    pos.breakeven_set = True
                    log.info(f"[BE] Breakeven set {sym}: SL->{pos.stop_loss:.4f}")

            hit_sl = hit_tp = False
            if pos.side == "BUY":
                hit_sl = price <= pos.stop_loss
                hit_tp = price >= pos.take_profit
            else:
                hit_sl = price >= pos.stop_loss
                hit_tp = price <= pos.take_profit

            if hit_tp:
                self._close_position(pos, price, "TP")
            elif hit_sl:
                self._close_position(pos, price, "SL")

    def _close_position(self, pos: Position, exit_price: float, reason: str):
        cs = CONTRACT_SIZES.get(pos.symbol, DEFAULT_CONTRACT_SIZE)
        if pos.side == "BUY":
            pnl = (exit_price - pos.entry_price) * pos.volume * cs
        else:
            pnl = (pos.entry_price - exit_price) * pos.volume * cs

        self.client.place_order(pos.symbol, pos.close_side, pos.volume)
        self._save_trade_close(pos, exit_price, pnl)
        self.risk.update_pnl(pnl)

        with self._lock:
            self._positions.pop(pos.symbol, None)

        status = "[WIN]" if pnl > 0 else "[LOSS]"
        log.info(f"{status} CLOSED {pos.side} {pos.symbol} [{reason}] | exit={exit_price} pnl={pnl:+.2f} USDT")
        self._emit("trade_closed", {
            "symbol": pos.symbol, "side": pos.side,
            "entry": pos.entry_price, "exit": exit_price,
            "pnl": round(pnl, 4), "reason": reason,
            "order_id": pos.order_id, "time": datetime.utcnow().isoformat()
        })

        record = {
            "symbol": pos.symbol, "side": pos.side,
            "entry": pos.entry_price, "exit": exit_price,
            "pnl": round(pnl, 4), "reason": reason,
            "time": datetime.utcnow().isoformat()
        }
        self._trade_log.append(record)

    # ------------------- QUERY ----------------

    def get_open_positions(self) -> list:
        with self._lock:
            return [vars(p) for p in self._positions.values()]

    def get_trade_log(self, limit: int = 50) -> list:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT symbol,side,entry_price,exit_price,pnl,status,opened_at,closed_at,reason "
                    "FROM trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            cols = ["symbol","side","entry","exit","pnl","status","opened_at","closed_at","reason"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return self._trade_log[-limit:]

    def get_stats(self) -> dict:
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), "
                    "SUM(pnl), MAX(pnl), MIN(pnl) FROM trades WHERE status='closed'"
                ).fetchone()
            total, wins, total_pnl, max_win, max_loss = row
            total = total or 0
            wins  = wins  or 0
            return {
                "total_trades": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total else 0,
                "total_pnl": round(total_pnl or 0, 2),
                "max_win": round(max_win or 0, 2),
                "max_loss": round(max_loss or 0, 2),
            }
        except Exception:
            return {}
