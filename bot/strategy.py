"""
Intelligent Strategy v2 - learns from trade history, uses ADX regime filter,
dynamic entry scoring, loss-streak awareness, and breakeven stop signalling.

Key improvements over v1:
  - ADX > 20 required (trend regime - no trading in chop)
  - Trades only WITH the 5-min trend (higher-timeframe bias)
  - Loss-streak cooldown: 2 consecutive SL -> extended pause per symbol
  - Minimum score raised to 8/12
  - Dynamic confidence floor based on recent win-rate
  - Price momentum confirmation (last 3 candles direction)
"""
from dataclasses import dataclass
from typing      import Dict, Optional, Tuple
import time
import sqlite3

import numpy  as np
import pandas as pd

from bot.ai_model    import AIModel
from bot.ai_analyst  import AIAnalyst
from bot.indicators  import compute_indicators
from bot.logger      import get_logger

log = get_logger("strategy")


@dataclass
class TradeSignal:
    symbol:     str
    signal:     str          # BUY / SELL / HOLD
    confidence: float
    price:      float
    atr:        float
    rsi:        float
    sl_mult:    float = 1.5  # dynamic SL multiplier
    tp_mult:    float = 3.0  # dynamic TP multiplier (1:2 R:R minimum)
    reason:     str  = ""
    score:      int  = 0


class Strategy:
    MIN_SCORE     = 8        # need >=8/12 (was 7)
    MIN_ADX       = 18       # trend strength gate
    MAX_SL_LOSSES = 2        # consecutive SL before extended pause
    BASE_COOLDOWN = 60       # seconds between trades (same symbol)
    LOSS_COOLDOWN = 300      # 5-min pause after 2 consecutive SL hits
    DB_PATH       = "data/trades.db"

    def __init__(self, model: AIModel, min_confidence: float = 0.60,
                 cooldown_seconds: int = 60,
                 analyst: Optional["AIAnalyst"] = None):
        self.model          = model
        self.analyst        = analyst    # may be None - gracefully skipped
        self.min_confidence = min_confidence
        self.base_cooldown  = cooldown_seconds
        self._last_trade_ts: Dict[str, float]  = {}
        self._last_reason:   Dict[str, str]    = {}   # last close reason per symbol
        self._sl_streak:     Dict[str, int]    = {}   # consecutive SL hits

    # ------------------- Learning from history -------------------

    def _load_streak(self, symbol: str) -> int:
        """Read consecutive SL streak from trade DB."""
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT reason FROM trades WHERE symbol=? AND status='closed' "
                    "ORDER BY id DESC LIMIT 5", (symbol,)
                ).fetchall()
            streak = 0
            for (reason,) in rows:
                if reason and "SL" in reason.upper():
                    streak += 1
                else:
                    break
            return streak
        except Exception:
            return self._sl_streak.get(symbol, 0)

    def _recent_win_rate(self, symbol: str, n: int = 10) -> float:
        """Win-rate on last N closed trades for this symbol."""
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT pnl FROM trades WHERE symbol=? AND status='closed' "
                    "ORDER BY id DESC LIMIT ?", (symbol, n)
                ).fetchall()
            if not rows:
                return 0.5
            wins = sum(1 for (p,) in rows if p and p > 0)
            return wins / len(rows)
        except Exception:
            return 0.5

    def mark_traded(self, symbol: str, reason: str = ""):
        self._last_trade_ts[symbol] = time.time()
        if reason:
            self._last_reason[symbol] = reason
            if "SL" in reason.upper():
                self._sl_streak[symbol] = self._sl_streak.get(symbol, 0) + 1
            else:
                self._sl_streak[symbol] = 0

    def _in_cooldown(self, symbol: str) -> Tuple[bool, str]:
        streak = self._load_streak(symbol)
        self._sl_streak[symbol] = streak

        # Extended cooldown after consecutive SL hits
        if streak >= self.MAX_SL_LOSSES:
            cd = self.LOSS_COOLDOWN
            label = f"{streak}-SL streak cooldown ({cd}s)"
        else:
            cd = self.base_cooldown
            label = f"cooldown ({cd}s)"

        elapsed = time.time() - self._last_trade_ts.get(symbol, 0)
        if elapsed < cd:
            return True, f"{label} - {int(cd-elapsed)}s left"
        return False, ""

    # ------------------- ADX (trend regime) -------------------

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> float:
        """Manual ADX calculation."""
        try:
            high  = df["high"].values.astype(float)
            low   = df["low"].values.astype(float)
            close = df["close"].values.astype(float)
            if len(close) < period + 5:
                return 0.0
            tr   = np.maximum(high[1:] - low[1:],
                   np.maximum(abs(high[1:] - close[:-1]),
                               abs(low[1:]  - close[:-1])))
            dmp  = np.where((high[1:]-high[:-1]) > (low[:-1]-low[1:]),
                            np.maximum(high[1:]-high[:-1], 0), 0)
            dmn  = np.where((low[:-1]-low[1:]) > (high[1:]-high[:-1]),
                            np.maximum(low[:-1]-low[1:],  0), 0)
            # Wilder smoothing
            def wilder(x, p):
                s = np.zeros(len(x))
                s[p-1] = x[:p].sum()
                for i in range(p, len(x)):
                    s[i] = s[i-1] - s[i-1]/p + x[i]
                return s
            atr14 = wilder(tr,  period)
            dp14  = wilder(dmp, period)
            dn14  = wilder(dmn, period)
            with np.errstate(divide="ignore", invalid="ignore"):
                pdi = np.where(atr14 > 0, 100 * dp14 / atr14, 0)
                ndi = np.where(atr14 > 0, 100 * dn14 / atr14, 0)
                dx  = np.where((pdi+ndi) > 0, 100*abs(pdi-ndi)/(pdi+ndi), 0)
            adx = wilder(dx[period-1:], period)
            return float(adx[-1]) if len(adx) else 0.0
        except Exception:
            return 0.0

    # ------------------- HTF trend bias -------------------

    @staticmethod
    def _trend_bias(df: pd.DataFrame) -> str:
        """Return 'UP', 'DOWN', or 'FLAT' from EMA crossover on the passed df."""
        try:
            df_i = compute_indicators(df)
            if df_i is None or len(df_i) < 3:
                return "FLAT"
            e21 = float(df_i["ema_21"].iloc[-1])
            e50 = float(df_i["ema_50"].iloc[-1])
            price = float(df_i["close"].iloc[-1])
            if price > e21 > e50:
                return "UP"
            if price < e21 < e50:
                return "DOWN"
            return "FLAT"
        except Exception:
            return "FLAT"

    # ------------------- Dynamic SL/TP multipliers -------------------

    def _dynamic_mult(self, symbol: str, atr: float, price: float,
                      adx: float) -> Tuple[float, float]:
        """
        Widen SL in high volatility / high ADX; tighten in calm markets.
        Base: SL=1.5xATR, TP=3.0xATR.
        ADX  > 35 -> SL=2xATR, TP=4xATR (trending strongly)
        ADX  < 20 -> SL=1.2xATR, TP=2.5xATR (borderline - very tight)
        """
        sl_pct = atr / price if price > 0 else 0.001
        if adx >= 35:
            sl_m, tp_m = 2.0, 4.0
        elif adx >= 25:
            sl_m, tp_m = 1.5, 3.0
        else:
            sl_m, tp_m = 1.2, 2.5

        # If recent win rate is low, go slightly wider
        wr = self._recent_win_rate(symbol)
        if wr < 0.35:
            sl_m = min(sl_m + 0.3, 2.5)
            tp_m = min(tp_m + 0.5, 4.5)

        return sl_m, tp_m

    # ------------------- Main evaluate -------------------

    def evaluate(self, symbol: str, df: pd.DataFrame,
                 current_price: float,
                 htf_df: Optional[pd.DataFrame] = None) -> TradeSignal:
        none = TradeSignal(symbol, "HOLD", 0.0, current_price, 0.0, 50.0)

        # 1. Cooldown / loss-streak gate
        in_cd, cd_reason = self._in_cooldown(symbol)
        if in_cd:
            return TradeSignal(symbol, "HOLD", 0.0, current_price, 0.0, 50.0,
                               reason=cd_reason)

        # 2. Compute indicators
        df_ind = compute_indicators(df)
        if df_ind is None or len(df_ind) < 30:
            return none

        latest = df_ind.iloc[-1]
        prev   = df_ind.iloc[-2]
        price  = current_price if current_price > 0 else float(latest["close"])
        atr    = float(latest.get("atr", 0))

        if atr <= 0 or price <= 0:
            return none

        # 3. ADX regime gate - skip choppy markets
        adx = self._adx(df_ind)
        if adx < self.MIN_ADX:
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, 50.0,
                               reason=f"Choppy market ADX={adx:.1f} < {self.MIN_ADX}")

        # 4. HTF trend bias
        htf_bias = self._trend_bias(htf_df) if htf_df is not None else "FLAT"

        # 5. Extract indicators
        ema5   = float(latest.get("ema_5",  price))
        ema9   = float(latest.get("ema_9",  price))
        ema21  = float(latest.get("ema_21", price))
        ema50  = float(latest.get("ema_50", price))
        macd_d = float(latest.get("macd_diff", 0))
        rsi    = float(latest.get("rsi",    50))
        rsi_sl = float(latest.get("rsi_slope", 0))
        stk    = float(latest.get("stoch_k", 50))
        std    = float(latest.get("stoch_d", 50))
        bb_pct = float(latest.get("bb_pct",  0.5))
        bb_w   = float(latest.get("bb_width", 0))
        vwap_d = float(latest.get("price_vs_vwap", 0))
        obv_sl = float(latest.get("obv_slope", 0))
        vol_r  = float(latest.get("vol_ratio", 1.0))
        body   = float(latest.get("body_pct", 0))
        c_dir  = float(latest.get("candle_dir", 0))
        roc3   = float(latest.get("roc_3", 0))

        # 6. Momentum confirmation: last 3 candle closes must agree
        closes = df_ind["close"].values[-4:]
        mom_up   = closes[-1] > closes[-2] > closes[-3]
        mom_down = closes[-1] < closes[-2] < closes[-3]

        # 7. Score system (max 12)
        long_s = short_s = 0

        # Trend alignment with EMAs
        if ema5 > ema9 > ema21:        long_s  += 2
        if ema5 < ema9 < ema21:        short_s += 2
        if price > ema50:              long_s  += 1
        else:                          short_s += 1

        # HTF bias alignment (critical)
        if htf_bias == "UP":           long_s  += 2
        elif htf_bias == "DOWN":       short_s += 2
        # FLAT: no bonus - either direction harder to trade

        # MACD
        if macd_d > 0:                 long_s  += 1
        elif macd_d < 0:               short_s += 1

        # RSI - avoid extremes, reward slopes
        if 35 < rsi < 60 and rsi_sl > 0:  long_s  += 1
        if 40 < rsi < 65 and rsi_sl < 0:  short_s += 1
        if rsi < 30:                   long_s  += 1   # oversold bounce
        if rsi > 70:                   short_s += 1   # overbought rejection

        # Stochastic crossover
        if stk < 80 and stk > std:     long_s  += 1
        if stk > 20 and stk < std:     short_s += 1

        # VWAP
        if vwap_d > 0:                 long_s  += 1
        elif vwap_d < 0:               short_s += 1

        # Volume spike
        if vol_r >= 1.2:
            long_s  += 1
            short_s += 1

        # Momentum (last 3 candles)
        if mom_up:                     long_s  += 1
        elif mom_down:                 short_s += 1

        # OBV
        if obv_sl > 0:                 long_s  += 1
        elif obv_sl < 0:               short_s += 1

        log.debug(f"{symbol}: L={long_s} S={short_s} ADX={adx:.1f} HTF={htf_bias} "
                  f"RSI={rsi:.1f} MACD={'UP' if macd_d>0 else 'DOWN'}")

        # 8. Direction decision
        if long_s >= self.MIN_SCORE and long_s > short_s:
            rule_dir  = "BUY"
            rule_conf = min(0.5 + long_s * 0.03, 0.90)
            rule_score = long_s
        elif short_s >= self.MIN_SCORE and short_s > long_s:
            rule_dir  = "SELL"
            rule_conf = min(0.5 + short_s * 0.03, 0.90)
            rule_score = short_s
        else:
            reason = f"Score too low L={long_s}/S={short_s} ADX={adx:.1f} HTF={htf_bias}"
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi, reason=reason)

        # 9. HTF contradiction veto - don't fight strong trend
        if htf_bias == "UP"   and rule_dir == "SELL" and adx > 30:
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason="SELL vetoed - strong HTF uptrend")
        if htf_bias == "DOWN" and rule_dir == "BUY"  and adx > 30:
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason="BUY vetoed - strong HTF downtrend")

        # 10. AI confirmation
        ai_signal, ai_conf = self.model.predict(symbol, df)
        if rule_dir == ai_signal:
            final_conf = min((rule_conf + ai_conf) / 2 + 0.05, 0.98)
        elif ai_signal == "HOLD":
            if rule_score >= 10:
                final_conf = rule_conf         # strong rule alone
            else:
                return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                                   reason=f"AI=HOLD, score={rule_score} not strong enough")
        else:
            # AI opposes - skip
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"AI disagrees: rule={rule_dir} AI={ai_signal}")

        # 11. Confidence floor (adaptive based on recent win-rate)
        wr = self._recent_win_rate(symbol)
        min_conf = self.min_confidence + max(0, (0.5 - wr) * 0.2)  # harder after losses
        if final_conf < min_conf:
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"Conf {final_conf:.2f} < floor {min_conf:.2f} (WR={wr:.0%})")

        # 12. Dynamic SL/TP multipliers
        sl_m, tp_m = self._dynamic_mult(symbol, atr, price, adx)

        streak = self._sl_streak.get(symbol, 0)
        reason = (f"score={rule_score}/12 ADX={adx:.0f} HTF={htf_bias} "
                  f"AI={ai_signal}({ai_conf:.2f}) RSI={rsi:.0f} "
                  f"SLx{sl_m} TPx{tp_m} WR={wr:.0%} streak={streak}SL")

        sig = TradeSignal(
            symbol=symbol, signal=rule_dir, confidence=round(final_conf, 3),
            price=price, atr=atr, rsi=rsi,
            sl_mult=sl_m, tp_mult=tp_m,
            reason=reason, score=rule_score
        )

        # 13. AI Analyst - narrative + risk veto (non-blocking; skipped if no analyst)
        if self.analyst is not None:
            try:
                risk_ins = self.analyst.risk_check(symbol, df, [])
                if risk_ins.risk_level == "HIGH":
                    log.warning(f"[WARN] AI risk veto {symbol}: {risk_ins.content}")
                    return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                                      reason=f"AI risk veto (HIGH): {risk_ins.content[:80]}")
                trade_ins = self.analyst.analyze_trade(sig, df)
                sig.reason = sig.reason + " | " + trade_ins.content
            except Exception as e:
                log.debug(f"Analyst error (non-fatal): {e}")

        log.info(f"[OK] [{rule_dir}] {symbol} | {reason}")
        return sig
