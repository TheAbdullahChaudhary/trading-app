"""
AI Analyst — real-time market analysis using Google Gemini LLM.
Falls back to rule-based NLP when GEMINI_API_KEY is not set.
"""
import os
import time
import threading
from typing import Optional, Dict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.logger import get_logger

log = get_logger("ai_analyst")

try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False
    log.warning("google-genai not installed — rule-based analyst active")


@dataclass
class AIInsight:
    symbol:       str
    insight_type: str         # "trade" | "regime" | "risk" | "chat"
    content:      str
    risk_level:   str = "LOW" # LOW | MEDIUM | HIGH
    regime:       str = "UNKNOWN"
    score:        int = 0
    timestamp:    float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "symbol":     self.symbol,
            "type":       self.insight_type,
            "content":    self.content,
            "risk_level": self.risk_level,
            "regime":     self.regime,
            "score":      self.score,
            "timestamp":  self.timestamp,
            "time_str":   time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }


class AIAnalyst:
    """
    Real-time AI analyst that wraps Google Gemini with a full rule-based fallback.
    Provides: trade narratives, regime commentary, risk alerts, and free-form chat.
    """
    SYSTEM_PROMPT = (
        "You are an expert algorithmic futures trading analyst for a MEXC scalping bot. "
        "Provide concise, data-driven analysis in 2-3 sentences max. "
        "Use trader terminology. Be direct. Reference specific numbers from the data."
    )
    MAX_INSIGHTS    = 100
    REGIME_CACHE_S  = 60     # cache regime per symbol for N seconds
    API_RATE_LIMIT  = 1.5    # min seconds between Gemini calls

    def __init__(self, api_key: Optional[str] = None):
        self._lock          = threading.Lock()
        self._insights:     list = []
        self._regime_cache: Dict[str, tuple] = {}  # sym -> (AIInsight, ts)
        self._last_api_ts   = 0.0
        self._client        = None
        self._use_gemini    = False

        if HAS_GEMINI and api_key:
            try:
                self._client = genai.Client(api_key=api_key)
                self._use_gemini = True
                log.info("AI Analyst active (gemini-2.0-flash via google.genai)")
            except Exception as e:
                log.warning(f"Gemini init failed: {e} -- rule-based fallback active")
                self._client = None
        else:
            self._client = None
            log.info("AI Analyst running in rule-based mode (no GEMINI_API_KEY)")

    # ------------------- Public API -------------------

    def analyze_trade(self, sig, df: pd.DataFrame) -> AIInsight:
        """Generate narrative for a trade signal."""
        ctx = self._trade_context(sig, df)
        if self._use_gemini:
            prompt = (f"Analyze this {sig.signal} signal for {sig.symbol}:\n{ctx}\n"
                      f"Explain WHY this is a {sig.signal} in 2 sentences.")
            content = self._gemini(prompt)
        else:
            content = self._rule_trade(sig, df)

        ins = AIInsight(
            symbol=sig.symbol, insight_type="trade", content=content,
            risk_level=self._risk_from_df(df, []),
            regime=self._regime_str(sig.symbol, df),
            score=sig.score,
        )
        self._store(ins)
        return ins

    def market_regime(self, symbol: str, df: pd.DataFrame) -> AIInsight:
        """Regime commentary, cached per symbol."""
        cached = self._regime_cache.get(symbol)
        if cached and (time.time() - cached[1]) < self.REGIME_CACHE_S:
            return cached[0]

        regime = self._regime_str(symbol, df)
        ctx    = self._regime_context(symbol, df)
        if self._use_gemini:
            prompt = (f"Describe the market regime for {symbol}:\n{ctx}\n"
                      "Give a 2-sentence assessment for a scalper.")
            content = self._gemini(prompt)
        else:
            content = self._rule_regime(symbol, df, regime)

        ins = AIInsight(symbol=symbol, insight_type="regime",
                        content=content, regime=regime)
        self._store(ins)
        self._regime_cache[symbol] = (ins, time.time())
        return ins

    def risk_check(self, symbol: str, df: pd.DataFrame,
                   open_positions: list) -> AIInsight:
        """Risk assessment for current conditions."""
        risk   = self._risk_from_df(df, open_positions)
        regime = self._regime_str(symbol, df)

        if self._use_gemini and risk in ("MEDIUM", "HIGH"):
            ctx = self._risk_context(symbol, df, open_positions, risk)
            prompt = (f"Risk={risk} for {symbol}:\n{ctx}\n"
                      "In 2 sentences, explain the risk and what a scalper should do.")
            content = self._gemini(prompt)
        else:
            content = self._rule_risk(symbol, df, risk)

        ins = AIInsight(symbol=symbol, insight_type="risk",
                        content=content, risk_level=risk, regime=regime)
        if risk in ("MEDIUM", "HIGH"):
            self._store(ins)
        return ins

    def ask(self, question: str, market_context: str = "") -> str:
        """Free-form Q&A for the dashboard chat panel."""
        if self._use_gemini:
            prompt = (f"Market context:\n{market_context}\n\n"
                      f"Trader asks: {question}\nAnswer in <=3 sentences.")
            return self._gemini(prompt)
        return self._rule_answer(question)

    def get_insights(self, limit: int = 30) -> list:
        with self._lock:
            return [i.to_dict() for i in self._insights[-limit:]]

    def get_regimes(self) -> Dict[str, str]:
        with self._lock:
            return {sym: ins.regime for sym, (ins, _) in self._regime_cache.items()}

    def is_gemini_active(self) -> bool:
        return self._use_gemini

    # ------------------- Gemini call -------------------

    def _gemini(self, prompt: str) -> str:
        try:
            elapsed = time.time() - self._last_api_ts
            if elapsed < self.API_RATE_LIMIT:
                time.sleep(self.API_RATE_LIMIT - elapsed)
            full_prompt = self.SYSTEM_PROMPT + "\n\n" + prompt
            resp = self._client.models.generate_content(
                model="gemini-2.0-flash",
                contents=full_prompt,
            )
            self._last_api_ts = time.time()
            return resp.text.strip()
        except Exception as e:
            log.warning(f"Gemini API error: {e}")
            return f"[AI API error -- rule-based mode: {str(e)[:80]}]"

    # ------------------- Context builders -------------------

    def _trade_context(self, sig, df: pd.DataFrame) -> str:
        r = self._last_row(df)
        if r is None:
            return f"Signal:{sig.signal} Price:{sig.price} Conf:{sig.confidence:.0%}"
        return (
            f"Symbol:{sig.symbol} Signal:{sig.signal} Price:{sig.price:.4f}\n"
            f"Score:{sig.score}/12 Confidence:{sig.confidence:.0%}\n"
            f"RSI:{r.get('rsi',50):.1f} MACD_diff:{r.get('macd_diff',0):.5f}\n"
            f"EMA5/9/21:{r.get('ema_5',0):.4f}/{r.get('ema_9',0):.4f}/{r.get('ema_21',0):.4f}\n"
            f"ATR:{sig.atr:.4f} VolRatio:{r.get('vol_ratio',1):.2f}x\n"
            f"BB%:{r.get('bb_pct',0.5):.2f} vsVWAP:{r.get('price_vs_vwap',0):.3f}\n"
            f"StochK/D:{r.get('stoch_k',50):.1f}/{r.get('stoch_d',50):.1f}\n"
            f"SL x {sig.sl_mult} TP x {sig.tp_mult}"
        )

    def _regime_context(self, symbol: str, df: pd.DataFrame) -> str:
        r = self._last_row(df)
        if r is None:
            return f"Symbol:{symbol}"
        adx = self._adx(df)
        return (
            f"Symbol:{symbol} Price:{r.get('close',0):.4f}\n"
            f"ADX:{adx:.1f} BBWidth:{r.get('bb_width',0):.4f} VolRatio:{r.get('vol_ratio',1):.2f}x\n"
            f"EMA21:{r.get('ema_21',0):.4f} EMA50:{r.get('ema_50',0):.4f}\n"
            f"RSI:{r.get('rsi',50):.1f} ROC5:{r.get('roc_5',0)*100:.2f}%"
        )

    def _risk_context(self, symbol: str, df: pd.DataFrame,
                      positions: list, risk: str) -> str:
        r    = self._last_row(df)
        pnl  = sum(p.get("unrealized_pnl", 0) for p in positions)
        rsi  = r.get("rsi", 50) if r else 50
        volr = r.get("vol_ratio", 1) if r else 1
        return (
            f"Symbol:{symbol} Risk:{risk}\n"
            f"OpenPositions:{len(positions)} UnrealPnL:{pnl:.2f}USDT\n"
            f"RSI:{rsi:.1f} VolRatio:{volr:.2f}x"
        )

    # ------------------- Rule-based narrators -------------------

    def _rule_trade(self, sig, df: pd.DataFrame) -> str:
        r = self._last_row(df)
        rsi  = r.get("rsi", 50)    if r else 50
        macd = r.get("macd_diff", 0) if r else 0
        vol  = r.get("vol_ratio", 1) if r else 1
        bb   = r.get("bb_pct", 0.5) if r else 0.5
        ema5 = r.get("ema_5", 0)   if r else 0
        ema21= r.get("ema_21", 0)  if r else 0

        parts = []
        if sig.signal == "BUY":
            if rsi < 45:        parts.append(f"RSI oversold at {rsi:.0f}")
            elif rsi < 60:      parts.append(f"RSI bullish at {rsi:.0f}")
            if macd > 0:        parts.append("MACD bullish crossover")
            if ema5 > ema21:    parts.append("EMA5 > EMA21 uptrend")
            if vol >= 1.5:      parts.append(f"volume spike {vol:.1f}x")
            if bb < 0.3:        parts.append("near BB lower band")
        else:
            if rsi > 60:        parts.append(f"RSI overbought at {rsi:.0f}")
            elif rsi > 45:      parts.append(f"RSI bearish at {rsi:.0f}")
            if macd < 0:        parts.append("MACD bearish crossover")
            if ema5 < ema21:    parts.append("EMA5 < EMA21 downtrend")
            if vol >= 1.5:      parts.append(f"volume spike {vol:.1f}x")
            if bb > 0.7:        parts.append("near BB upper band")
        if not parts:
            parts.append(f"score {sig.score}/12 passed threshold")
        return (f"{sig.signal} {sig.symbol}: " + ", ".join(parts[:3]) +
                f". Confidence {sig.confidence:.0%}, SL x {sig.sl_mult} TP x {sig.tp_mult}.")

    def _rule_regime(self, symbol: str, df: pd.DataFrame, regime: str) -> str:
        r   = self._last_row(df)
        adx = self._adx(df)
        rsi = r.get("rsi", 50) if r else 50
        vol = r.get("vol_ratio", 1) if r else 1
        msgs = {
            "TRENDING_UP":   f"{symbol} bullish trend (ADX={adx:.0f}, RSI={rsi:.0f}). Price above EMA21/50. Favor long scalps.",
            "TRENDING_DOWN": f"{symbol} bearish trend (ADX={adx:.0f}, RSI={rsi:.0f}). Price below EMA21/50. Favor short scalps.",
            "CHOPPY":        f"{symbol} choppy range (ADX={adx:.0f}). Avoid trend trades; wait for breakout.",
            "HIGH_VOL":      f"{symbol} high volatility (vol {vol:.1f}x, ADX={adx:.0f}). Widen SL or reduce size.",
        }
        return msgs.get(regime, f"{symbol} regime: {regime}. ADX={adx:.0f}, RSI={rsi:.0f}.")

    def _rule_risk(self, symbol: str, df: pd.DataFrame, risk: str) -> str:
        return {
            "LOW":    f"{symbol}: Risk LOW -- normal conditions, standard sizing.",
            "MEDIUM": f"{symbol}: Risk MEDIUM -- elevated volatility, consider 30% smaller size.",
            "HIGH":   f"{symbol}: Risk HIGH -- extreme conditions. No new entries; tighten stops.",
        }.get(risk, f"{symbol}: Risk {risk}.")

    def _rule_answer(self, question: str) -> str:
        q = question.lower()
        if any(w in q for w in ["buy", "long"]):
            return "For longs: RSI < 50, MACD bullish, EMA5 > EMA21, ADX > 18. Volume spike confirms."
        if any(w in q for w in ["sell", "short"]):
            return "For shorts: RSI > 55, MACD turning negative, EMA5 < EMA21 with volume. ADX > 18 required."
        if any(w in q for w in ["risk", "safe", "danger"]):
            return "Use ATR-based SL, max 2% risk/trade. Avoid HIGH_VOL regime without widened stops."
        if any(w in q for w in ["regime", "trend", "market"]):
            return "ADX > 25 = trending; < 18 = choppy. Align entries with 5-min HTF bias for best results."
        return "Check the AI Analyst panel for live signal narratives and regime badges per symbol."

    # ------------------- Helpers -------------------

    def _last_row(self, df: pd.DataFrame):
        """Get last row with indicators computed."""
        from bot.indicators import compute_indicators
        try:
            df_i = compute_indicators(df)
            if df_i is not None and not df_i.empty:
                return df_i.iloc[-1]
        except Exception:
            pass
        return None

    def _risk_from_df(self, df: pd.DataFrame, positions: list) -> str:
        r = self._last_row(df)
        if r is None:
            return "MEDIUM"
        score = 0
        rsi = r.get("rsi", 50)
        vol = r.get("vol_ratio", 1)
        bbw = r.get("bb_width", 0.02)
        if rsi > 75 or rsi < 25:  score += 2
        elif rsi > 68 or rsi < 32: score += 1
        if vol > 2.5:  score += 2
        elif vol > 1.8: score += 1
        if bbw > 0.05: score += 1
        if len(positions) >= 2: score += 1
        if score >= 4: return "HIGH"
        if score >= 2: return "MEDIUM"
        return "LOW"

    def _regime_str(self, symbol: str, df: pd.DataFrame) -> str:
        r = self._last_row(df)
        if r is None:
            return "UNKNOWN"
        adx   = self._adx(df)
        price = float(r.get("close", 0))
        ema21 = float(r.get("ema_21", price))
        ema50 = float(r.get("ema_50", price))
        vol   = float(r.get("vol_ratio", 1))
        if vol > 2.5:              return "HIGH_VOL"
        if adx < 18:               return "CHOPPY"
        if price > ema21 > ema50:  return "TRENDING_UP"
        if price < ema21 < ema50:  return "TRENDING_DOWN"
        return "CHOPPY"

    def _adx(self, df: pd.DataFrame, period: int = 14) -> float:
        try:
            h = df["high"].values.astype(float)
            l = df["low"].values.astype(float)
            c = df["close"].values.astype(float)
            if len(c) < period + 5:
                return 0.0
            tr  = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
            dmp = np.where((h[1:]-h[:-1]) > (l[:-1]-l[1:]), np.maximum(h[1:]-h[:-1], 0), 0)
            dmn = np.where((l[:-1]-l[1:]) > (h[1:]-h[:-1]), np.maximum(l[:-1]-l[1:], 0), 0)
            def w(x, p):
                s = np.zeros(len(x)); s[p-1] = x[:p].sum()
                for i in range(p, len(x)): s[i] = s[i-1] - s[i-1]/p + x[i]
                return s
            a14 = w(tr, period); d14 = w(dmp, period); n14 = w(dmn, period)
            with np.errstate(divide="ignore", invalid="ignore"):
                pdi = np.where(a14>0, 100*d14/a14, 0)
                ndi = np.where(a14>0, 100*n14/a14, 0)
                dx  = np.where((pdi+ndi)>0, 100*abs(pdi-ndi)/(pdi+ndi), 0)
            adx = w(dx[period-1:], period)
            return float(adx[-1]) if len(adx) else 0.0
        except Exception:
            return 0.0

    def _store(self, ins: AIInsight):
        with self._lock:
            self._insights.append(ins)
            if len(self._insights) > self.MAX_INSIGHTS:
                self._insights = self._insights[-self.MAX_INSIGHTS:]
