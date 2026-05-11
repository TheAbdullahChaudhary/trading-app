"""Risk management for scalping — contract-size aware position sizing."""
import time
from collections import deque
from bot.logger import get_logger

log = get_logger("risk_manager")

# MEXC contract sizes (must match trader.py)
CONTRACT_SIZES = {
    "BTC_USDT":    0.0001,
    "ETH_USDT":    0.01,
    "XAUT_USDT":   0.001,
    "SILVER_USDT": 0.01,
    "USOIL_USDT":  0.01,
}
DEFAULT_CONTRACT_SIZE = 1.0


class RiskManager:
    def __init__(self, risk_pct: float = 2.0,
                 max_positions: int = 2,
                 max_drawdown_pct: float = 10.0,
                 sl_atr_mult: float = 1.0,
                 tp_atr_mult: float = 2.0,
                 max_trades_per_hour: int = 15,
                 cooldown_seconds: int = 30):
        self.risk_pct             = risk_pct
        self.max_positions        = max_positions
        self.max_drawdown_pct     = max_drawdown_pct
        self.sl_atr_mult          = sl_atr_mult
        self.tp_atr_mult          = tp_atr_mult
        self.max_trades_per_hour  = max_trades_per_hour
        self.cooldown_seconds     = cooldown_seconds

        self._start_balance    = None
        self._daily_pnl        = 0.0
        self._trade_timestamps = deque()

    # ── Daily reset ──────────────────────────────────────────

    def reset_daily(self, balance: float):
        self._start_balance = balance
        self._daily_pnl     = 0.0
        log.info(f"Daily reset | balance={balance:.2f} USDT")

    def update_pnl(self, pnl: float):
        self._daily_pnl += pnl

    # ── Trade frequency ───────────────────────────────────────

    def record_trade(self):
        now = time.time()
        self._trade_timestamps.append(now)
        while self._trade_timestamps and now - self._trade_timestamps[0] > 3600:
            self._trade_timestamps.popleft()

    def trades_this_hour(self) -> int:
        now = time.time()
        while self._trade_timestamps and now - self._trade_timestamps[0] > 3600:
            self._trade_timestamps.popleft()
        return len(self._trade_timestamps)

    # ── Gate check ───────────────────────────────────────────

    def is_trading_allowed(self, open_positions: int) -> tuple:
        if open_positions >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        if self.trades_this_hour() >= self.max_trades_per_hour:
            return False, f"Max trades/hour ({self.max_trades_per_hour}) hit"

        if self._start_balance and self._start_balance > 0:
            drawdown = -self._daily_pnl / self._start_balance * 100
            if drawdown >= self.max_drawdown_pct:
                return False, f"Daily drawdown limit hit ({drawdown:.1f}%)"

        return True, "OK"

    # ── Order sizing (contract-size aware) ────────────────────

    def calculate_order(self, balance: float, price: float,
                        atr: float, signal: str, symbol: str = "",
                        sl_mult: float = None, tp_mult: float = None) -> dict:
        """
        Risk-based sizing that accounts for contract size.
        sl_mult / tp_mult override config if provided (from strategy dynamic calc).
        """
        if atr <= 0 or price <= 0 or balance <= 0:
            return {}

        cs       = CONTRACT_SIZES.get(symbol, DEFAULT_CONTRACT_SIZE)
        sl_m     = sl_mult if sl_mult is not None else self.sl_atr_mult
        tp_m     = tp_mult if tp_mult is not None else self.tp_atr_mult

        risk_amount = balance * (self.risk_pct / 100)
        sl_dist     = atr * sl_m
        tp_dist     = atr * tp_m

        # USDT at risk per 1 contract
        usdt_per_contract = sl_dist * cs
        if usdt_per_contract <= 0:
            return {}

        # Volume: clamp between 1 and 500
        volume = max(1, min(500, round(risk_amount / usdt_per_contract)))

        if signal == "BUY":
            stop_loss   = round(price - sl_dist, 6)
            take_profit = round(price + tp_dist, 6)
            side        = 1   # Open Long
        else:
            stop_loss   = round(price + sl_dist, 6)
            take_profit = round(price - tp_dist, 6)
            side        = 3   # Open Short

        result = dict(
            volume=volume, stop_loss=stop_loss,
            take_profit=take_profit, side=side,
            sl_dist=sl_dist, tp_dist=tp_dist,
            risk_amount=round(risk_amount, 4),
            contract_size=cs
        )
        log.info(
            f"Order | {symbol} {signal} @{price:.4f} "
            f"SL={stop_loss:.4f}(-{sl_dist:.4f}) "
            f"TP={take_profit:.4f}(+{tp_dist:.4f}) "
            f"vol={volume} cs={cs} risk≈{risk_amount:.2f}U "
            f"SL×{sl_m:.1f} TP×{tp_m:.1f}"
        )
        return result
