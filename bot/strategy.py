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
    MIN_SCORE     = 8        # need >=8/12 - HIGH quality (relaxed from 9)
    MIN_ADX       = 23       # strong trend required (relaxed from 25)
    MAX_SL_LOSSES = 2        # pause after 2 losses (was 1)
    BASE_COOLDOWN = 180      # 3min between trades (was 300)
    LOSS_COOLDOWN = 360      # 6-min pause after losses (was 600)
    DB_PATH       = "data/trades.db"

    def __init__(self, model: AIModel, min_confidence: float = 0.60,
                 cooldown_seconds: int = 60,
                 analyst: Optional["AIAnalyst"] = None,
                 advanced_ai=None, predictive=None, multi_exchange=None):
        self.model          = model
        self.analyst        = analyst    # may be None - gracefully skipped
        self.advanced_ai    = advanced_ai  # Multi-model AI
        self.predictive     = predictive   # Predictive signals
        self.multi_exchange = multi_exchange  # Cross-exchange arbitrage
        self.min_confidence = 0.70  # High confidence (relaxed from 0.75)
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
        Optimized: Tighter stops, better risk/reward.
        Base: SL=1.8xATR, TP=4.0xATR (1:2.22 R:R).
        ADX  > 35 -> SL=2.0xATR, TP=5.0xATR (strong trend)
        ADX  < 30 -> SL=1.8xATR, TP=4.0xATR (moderate)
        """
        sl_pct = atr / price if price > 0 else 0.001
        if adx >= 35:
            sl_m, tp_m = 2.0, 5.0
        elif adx >= 30:
            sl_m, tp_m = 1.8, 4.0
        else:
            sl_m, tp_m = 1.8, 4.0

        # If recent win rate is low, tighten stops (not widen)
        wr = self._recent_win_rate(symbol)
        if wr < 0.40:
            sl_m = max(sl_m - 0.2, 1.5)  # Tighter stop
            tp_m = max(tp_m - 0.5, 3.0)  # Closer target

        return sl_m, tp_m

    # ------------------- Main evaluate -------------------

    def evaluate(self, symbol: str, df: pd.DataFrame,
                 current_price: float,
                 htf_df: Optional[pd.DataFrame] = None) -> TradeSignal:
        none = TradeSignal(symbol, "HOLD", 0.0, current_price, 0.0, 50.0)

        # 0. TIME FILTER - Avoid low liquidity periods (weekends, late night UTC)
        from datetime import datetime
        now = datetime.utcnow()
        hour = now.hour
        weekday = now.weekday()
        
        # Skip weekends (Saturday=5, Sunday=6)
        if weekday >= 5:
            return TradeSignal(symbol, "HOLD", 0.0, current_price, 0.0, 50.0,
                               reason="Weekend - low liquidity")
        
        # Skip low liquidity hours (0-4 UTC, 22-24 UTC)
        if hour < 4 or hour >= 22:
            return TradeSignal(symbol, "HOLD", 0.0, current_price, 0.0, 50.0,
                               reason=f"Low liquidity hour {hour}:00 UTC")

        # 1. Cooldown / loss-streak gate
        in_cd, cd_reason = self._in_cooldown(symbol)
        if in_cd:
            return TradeSignal(symbol, "HOLD", 0.0, current_price, 0.0, 50.0,
                               reason=cd_reason)

        # 1.5 PREDICTIVE SIGNALS - Analyze tick data for early entry
        if self.predictive:
            early_signal = self.predictive.analyze_tick_data(
                symbol, current_price, 
                df['volume'].iloc[-1] if 'volume' in df.columns else 0,
                time.time()
            )
            if early_signal['strength'] >= 70:
                log.info(f"{symbol}: PREDICTIVE signal {early_signal['signal']} "
                        f"strength={early_signal['strength']}% - {early_signal['reason']}")
        
        # 1.6 ARBITRAGE SIGNALS - Check cross-exchange price differences
        if self.multi_exchange:
            self.multi_exchange.update_mexc_price(symbol, current_price)
            arb_signal = self.multi_exchange.get_signal(symbol)
            if arb_signal['strength'] >= 60:
                log.info(f"{symbol}: ARBITRAGE signal {arb_signal['direction']} "
                        f"strength={arb_signal['strength']:.0f}% - {arb_signal['reason']}")

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
            log.debug(f"{symbol}: Choppy market ADX={adx:.1f} < {self.MIN_ADX}")
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

        # 10. AI confirmation (ENHANCED with multi-model ensemble)
        ai_signal, ai_conf = self.model.predict(symbol, df)
        
        # 10.5 ADVANCED AI ENSEMBLE - Multi-model prediction
        ensemble_signal = None
        ensemble_conf = 0
        if self.advanced_ai:
            try:
                prediction = self.advanced_ai.predict_next_move(symbol, df)
                ensemble_signal = prediction['direction']
                ensemble_conf = prediction['confidence'] / 100
                
                if prediction['confidence'] >= 70:
                    log.info(f"{symbol}: ENSEMBLE predicts {ensemble_signal} "
                            f"conf={prediction['confidence']}% | {', '.join(prediction['signals'][:3])}")
                    
                    # If ensemble strongly agrees with rules, boost confidence
                    if ensemble_signal == rule_dir and prediction['model_agreement'] >= 0.67:
                        ai_conf = max(ai_conf, ensemble_conf)
                        log.info(f"{symbol}: Ensemble BOOST - using conf={ai_conf:.2f}")
            except Exception as e:
                log.debug(f"{symbol}: Ensemble prediction failed: {e}")
        
        if rule_dir == ai_signal:
            final_conf = min((rule_conf + ai_conf) / 2 + 0.05, 0.98)
        elif ai_signal == "HOLD":
            if rule_score >= 6:  # Balanced threshold
                final_conf = rule_conf         # strong rule alone
            else:
                return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                                   reason=f"AI=HOLD, score={rule_score} not strong enough")
        else:
            # AI opposes - skip
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"AI disagrees: rule={rule_dir} AI={ai_signal}")
        
        # 10.7 ARBITRAGE BOOST - If cross-exchange signal agrees, boost confidence
        if self.multi_exchange:
            arb_signal = self.multi_exchange.get_signal(symbol)
            if arb_signal['direction'] == rule_dir and arb_signal['strength'] >= 60:
                boost = min(arb_signal['strength'] / 1000, 0.10)  # Max 10% boost
                final_conf = min(final_conf + boost, 0.98)
                log.info(f"{symbol}: ARBITRAGE BOOST +{boost:.2f} → conf={final_conf:.2f}")

        # 11. Confidence floor (adaptive based on recent win-rate)
        wr = self._recent_win_rate(symbol)
        min_conf = self.min_confidence + max(0, (0.5 - wr) * 0.3)  # stricter after losses
        if final_conf < min_conf:
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"Conf {final_conf:.2f} < floor {min_conf:.2f} (WR={wr:.0%})")
        
        # EXTRA: Require momentum confirmation (BALANCED)
        if abs(roc3) < 0.002:  # 0.2% minimum momentum (relaxed from 0.3%)
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"Weak momentum ROC={roc3:.4f}")
        
        # EXTRA: Require volume confirmation (BALANCED)
        if vol_r < 1.1:  # volume must be 10% above average (relaxed from 1.2x)
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"Low volume ratio={vol_r:.2f}")
        
        # EXTRA: Volatility filter - avoid extreme volatility
        atr_pct = (atr / price) * 100 if price > 0 else 0
        if atr_pct > 4.0:  # ATR > 4% of price = too volatile (relaxed from 3%)
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"Extreme volatility ATR={atr_pct:.2f}%")
        
        # EXTRA: Bollinger Band filter - avoid extremes
        if bb_pct < 0.05 or bb_pct > 0.95:  # Too close to bands (relaxed from 0.1/0.9)
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"BB extreme position={bb_pct:.2f}")
        
        # EXTRA: RSI divergence check - avoid overbought/oversold extremes
        if rule_dir == "BUY" and rsi > 70:  # Relaxed from 65
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"BUY rejected - RSI overbought {rsi:.1f}")
        if rule_dir == "SELL" and rsi < 30:  # Relaxed from 35
            return TradeSignal(symbol, "HOLD", 0.0, price, atr, rsi,
                               reason=f"SELL rejected - RSI oversold {rsi:.1f}")

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
