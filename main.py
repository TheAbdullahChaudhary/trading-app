"""
MEXC Scalping AI Bot — Main Entry Point
Usage:
  python main.py               # Live scalping
  python main.py --dry-run     # Paper trade
  python main.py --test        # Test API connectivity
"""
import argparse
import os
import sys
import threading
import time

import yaml
from dotenv import load_dotenv

load_dotenv()

from bot.logger       import get_logger
from bot.mexc_client  import MEXCClient
from bot.data_fetcher import DataFetcher
from bot.ai_model     import AIModel
from bot.ai_analyst   import AIAnalyst
from bot.advanced_ai  import AdvancedAI
from bot.predictive_signals import PredictiveSignals
from bot.multi_exchange import MultiExchangeAggregator
from bot.strategy     import Strategy
from bot.risk_manager import RiskManager
from bot.trader       import Trader
from dashboard.app    import init_dashboard, run_dashboard, emit_ai_regime, emit_ai_insight

log = get_logger("main")


def load_config(path="config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class ScalpingBot:
    def __init__(self, cfg: dict, dry_run: bool):
        self._cfg     = cfg
        self._dry_run = dry_run
        self._running = False
        self._paused  = False

        api_key    = os.environ.get("MEXC_API_KEY",    "")
        api_secret = os.environ.get("MEXC_API_SECRET", "")
        gemini_key = os.environ.get("GEMINI_API_KEY",  "")

        symbols   = [s["name"] for s in cfg["symbols"]]
        rcfg      = cfg["risk"]
        ai_cfg    = cfg["ai"]
        tcfg      = cfg["trading"]

        self.client  = MEXCClient(api_key, api_secret, dry_run=dry_run)
        self.fetcher = DataFetcher(
            self.client, symbols,
            interval=tcfg["timeframe"],
            lookback=tcfg["lookback_candles"]
        )
        self.risk = RiskManager(
            risk_pct             = rcfg["risk_per_trade_pct"],
            max_positions        = rcfg["max_open_positions"],
            max_drawdown_pct     = rcfg["max_daily_drawdown_pct"],
            sl_atr_mult          = rcfg["sl_atr_multiplier"],
            tp_atr_mult          = rcfg["tp_atr_multiplier"],
            max_trades_per_hour  = rcfg.get("max_trades_per_hour", 20),
            cooldown_seconds     = rcfg.get("cooldown_seconds", 30),
        )
        self.model    = AIModel(
            min_confidence=ai_cfg["min_confidence"],
            lookback=ai_cfg["lookback_for_training"]
        )
        self.advanced_ai = AdvancedAI()  # Multi-model ensemble
        self.predictive = PredictiveSignals()  # Early signal detection
        self.multi_exchange = MultiExchangeAggregator()  # Cross-exchange arbitrage
        self.analyst  = AIAnalyst(api_key=gemini_key or None)
        self.strategy = Strategy(
            self.model,
            min_confidence=ai_cfg["min_confidence"],
            cooldown_seconds=rcfg.get("cooldown_seconds", 30),
            analyst=self.analyst,
            advanced_ai=self.advanced_ai,  # Pass advanced AI
            predictive=self.predictive,    # Pass predictive engine
            multi_exchange=self.multi_exchange,  # Pass multi-exchange
        )
        self.trader = Trader(self.client, self.risk, dry_run=dry_run)

        self._symbols    = symbols
        self._check_every = tcfg["signal_check_interval"]   # 15 s
        self._retrain_h  = ai_cfg["retrain_interval_hours"]
        self._lev_map    = {s["name"]: s["leverage"] for s in cfg["symbols"]}

    def bot_controller(self, cmd: str):
        if cmd == "start":
            self._running = True;  self._paused = False
            log.info("[START] Bot STARTED by dashboard")
        elif cmd == "stop":
            self._running = False
            log.info("[STOP] Bot STOPPED by dashboard")
        elif cmd == "pause":
            self._paused = not self._paused
            log.info(f"{'[PAUSE] PAUSED' if self._paused else '[RESUME] RESUMED'} by dashboard")

    # ------------------------------------------------------------

    def startup(self):
        log.info("=" * 60)
        log.info(f"  MEXC SCALPING AI BOT  |  dry_run={self._dry_run}")
        log.info(f"  Symbols : {', '.join(self._symbols)}")
        log.info(f"  Interval: {self._cfg['trading']['timeframe']} | Check: {self._check_every}s")
        log.info("=" * 60)

        if not self.client.test_connection():
            log.warning("[WARN] API test failed – running in reduced mode (public data only)")

        # Load historical 1-min candles
        log.info("Loading 1-minute historical data...")
        self.fetcher.load_history()

        # Also load 5-min HTF for trend bias (if configured)
        htf_tf = self._cfg["trading"].get("htf_timeframe", "Min5")
        self._htf_fetcher = DataFetcher(
            self.client, self._symbols,
            interval=htf_tf,
            lookback=100,
            db_path="data/trades_htf.db"
        )
        log.info("Loading 5-minute higher-timeframe data...")
        self._htf_fetcher.load_history()

        # Set leverage
        for sym in self._symbols:
            lev = self._lev_map.get(sym, 20)
            try:
                self.client.set_leverage(sym, lev, 1)
                self.client.set_leverage(sym, lev, 2)
                log.info(f"  Leverage set: {sym} x {lev}")
            except Exception:
                pass

        # Train / load AI models
        log.info("Initializing AI models...")
        for sym in self._symbols:
            if not self.model.load_model(sym):
                df = self.fetcher.get_df(sym)
                if df is not None:
                    self.model.train(sym, df)
            
            # Train advanced AI models
            df = self.fetcher.get_df(sym)
            if df is not None and len(df) >= 200:
                self.advanced_ai.train_models(sym, df)
                log.info(f"  Advanced AI trained: {sym}")

        # Subscribe WebSocket (1m real-time feed)
        self.client.subscribe_ws(
            self._symbols,
            on_kline =self.fetcher.on_kline,
            on_ticker=self.fetcher.on_ticker,
        )
        log.info("[OK] WebSocket live feed active")
        
        # Start multi-exchange aggregator in background
        import threading
        def run_multi_exchange():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.multi_exchange.start(self._symbols))
            except Exception as e:
                log.error(f"Multi-exchange error: {e}")
        
        exchange_thread = threading.Thread(target=run_multi_exchange, daemon=True)
        exchange_thread.start()
        log.info("[OK] Multi-exchange aggregator started (Binance, Coinbase)")

        # Daily balance reset
        balance = self._get_balance()
        self.risk.reset_daily(balance)
        log.info(f"Starting balance: {balance:.2f} USDT")

    def _get_balance(self) -> float:
        try:
            assets = self.client.get_account()
            if assets:
                usdt = next((a for a in assets if a.get("currency","").upper()=="USDT"), {})
                return float(usdt.get("availableBalance", usdt.get("equity", 0)))
        except Exception:
            pass
        # Demo fallback: 100 USDT when API keys not configured
        return 100.0

    # -- Main scalping loop -----------------------------------

    def run_scalping_loop(self):
        self._running = True  # AUTO-START enabled
        last_retrain  = time.time()
        last_balance  = time.time()

        log.info("[RUN] Scalping loop started - AUTO-TRADING ACTIVE")
        while True:
            # Auto-start: no need to wait for dashboard button
            # if not self._running or self._paused:
            #     time.sleep(1)
            #     continue
            
            if self._paused:  # Only check pause, not running flag
                time.sleep(1)
                continue

            try:
                prices = self.fetcher.get_all_prices()
                open_pos = self.trader.get_open_positions()
                
                log.debug(f"Loop tick: prices={len(prices)}, positions={len(open_pos)}, running={self._running}")

                # -- Monitor SL/TP ----------------------------
                self.trader.monitor_positions(prices)

                # -- Evaluate each symbol ---------------------
                # Count positions per symbol
                pos_counts = {}
                for p in open_pos:
                    s = p["symbol"]
                    pos_counts[s] = pos_counts.get(s, 0) + 1

                for sym in self._symbols:
                    allowed, reason = self.risk.is_trading_allowed(len(open_pos))
                    if not allowed:
                        log.debug(f"Skipping {sym}: {reason}")
                        continue

                    # No per-symbol limit - only global limit (max_open_positions: 10)

                    df    = self.fetcher.get_df(sym)
                    htf_df= self._htf_fetcher.get_df(sym)
                    price = prices.get(sym, 0)

                    if df is None or len(df) < 60 or price <= 0:
                        log.debug(f"{sym}: Insufficient data df={df is not None}, len={len(df) if df is not None else 0}, price={price}")
                        continue

                    log.info(f"Evaluating {sym} @ {price:.2f}")
                    sig = self.strategy.evaluate(sym, df, price, htf_df=htf_df)

                    if sig.signal in ("BUY", "SELL") and sig.atr > 0:
                        balance    = self._get_balance()
                        order_info = self.risk.calculate_order(
                            balance, sig.price, sig.atr, sig.signal,
                            symbol=sym,
                            sl_mult=sig.sl_mult,
                            tp_mult=sig.tp_mult,
                        )
                        if order_info:
                            opened = self.trader.open_position(sig, order_info)
                            if opened:
                                self.risk.record_trade()
                                self.strategy.mark_traded(sym)
                                open_pos = self.trader.get_open_positions()
                                open_syms = {p["symbol"] for p in open_pos}

                # -- Periodic retrain -------------------------
                if time.time() - last_retrain > self._retrain_h * 3600:
                    log.info("[RETRAIN] Retraining AI models on fresh data...")
                    for sym in self._symbols:
                        df = self.fetcher.get_df(sym)
                        if df is not None:
                            self.model.train(sym, df)
                    last_retrain = time.time()

                # -- Periodic balance refresh -----------------
                if time.time() - last_balance > 3600:
                    bal = self._get_balance()
                    self.risk.reset_daily(bal)
                    last_balance = time.time()

            except Exception as e:
                log.error(f"Scalp loop error: {e}", exc_info=True)

            time.sleep(self._check_every)


def main():
    parser = argparse.ArgumentParser(description="MEXC Scalping AI Bot")
    parser.add_argument("--dry-run", action="store_true", help="Paper trade (no real orders)")
    parser.add_argument("--test",    action="store_true", help="Test API connectivity")
    parser.add_argument("--config",  default="config.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.test:
        api_key    = os.environ.get("MEXC_API_KEY",    "")
        api_secret = os.environ.get("MEXC_API_SECRET", "")
        ok = MEXCClient(api_key, api_secret).test_connection()
        sys.exit(0 if ok else 1)

    dry_run = args.dry_run or cfg["bot"].get("dry_run", False)
    if dry_run:
        log.info("[DRY-RUN] DRY-RUN MODE - no real orders placed")

    bot = ScalpingBot(cfg, dry_run=dry_run)
    bot.startup()

    # Link dashboard
    dash = cfg["dashboard"]
    init_dashboard(bot.trader, bot.fetcher, bot.bot_controller,
                   analyst=bot.analyst, ai_model=bot.model)

    # Scalping loop -> background thread
    t = threading.Thread(target=bot.run_scalping_loop, daemon=True)
    t.start()

    log.info(f"[DASHBOARD] Dashboard -> http://localhost:{dash['port']}")
    run_dashboard(host=dash["host"], port=dash["port"])


if __name__ == "__main__":
    main()
