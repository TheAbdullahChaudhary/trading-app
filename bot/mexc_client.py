"""MEXC Futures REST + WebSocket API client."""
import hashlib
import hmac
import json
import threading
import time
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode

import requests
import websocket

from bot.logger import get_logger

log = get_logger("mexc_client")


class MEXCClient:
    """Handles all communication with MEXC Futures API."""

    BASE_URL = "https://contract.mexc.com"

    def __init__(self, api_key: str, api_secret: str, dry_run: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, List[Callable]] = {}
        self._ws_connected = threading.Event()
        log.info(f"MEXCClient initialized | dry_run={dry_run}")

    # ─────────────────────────── AUTH ────────────────────────────

    def _sign(self, params: dict) -> str:
        timestamp = str(int(time.time() * 1000))
        query = self.api_key + timestamp + urlencode(sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return timestamp, signature

    def _signed_headers(self, params: dict = None) -> dict:
        params = params or {}
        timestamp, signature = self._sign(params)
        return {
            "ApiKey": self.api_key,
            "Request-Time": timestamp,
            "Signature": signature,
            "Content-Type": "application/json",
        }

    # ─────────────────────────── REST ────────────────────────────

    def _get(self, path: str, params: dict = None, auth: bool = False) -> dict:
        params = params or {}
        url = self.BASE_URL + path
        headers = self._signed_headers(params) if auth else {}
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("success") is False:
                log.error(f"API error GET {path}: {data.get('message')}")
            return data
        except Exception as e:
            log.error(f"GET {path} failed: {e}")
            return {}

    def _post(self, path: str, body: dict = None, auth: bool = True) -> dict:
        body = body or {}
        url = self.BASE_URL + path
        timestamp = str(int(time.time() * 1000))
        body_str = json.dumps(body)
        sign_str = self.api_key + timestamp + body_str
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        headers = {
            "ApiKey": self.api_key,
            "Request-Time": timestamp,
            "Signature": signature,
            "Content-Type": "application/json",
        }
        try:
            resp = self.session.post(url, data=body_str, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"POST {path} failed: {e}")
            return {}

    # ─────────────────────── PUBLIC ENDPOINTS ────────────────────

    def get_klines(self, symbol: str, interval: str = "Min15", limit: int = 500) -> List[dict]:
        """Fetch OHLCV candles. Returns list of dicts with open/high/low/close/vol/time."""
        data = self._get(
            "/api/v1/contract/kline/" + symbol,
            params={"interval": interval, "limit": limit}
        )
        result = data.get("data", {})
        if not result:
            return []
        opens = result.get("open", [])
        highs = result.get("high", [])
        lows = result.get("low", [])
        closes = result.get("close", [])
        vols = result.get("vol", [])
        times = result.get("time", [])
        candles = [
            {
                "time": times[i],
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(vols[i]),
            }
            for i in range(len(closes))
        ]
        return candles

    def get_ticker(self, symbol: str) -> dict:
        data = self._get(f"/api/v1/contract/ticker?symbol={symbol}")
        return data.get("data", {})

    def get_depth(self, symbol: str) -> dict:
        data = self._get(f"/api/v1/contract/depth/{symbol}")
        return data.get("data", {})

    # ─────────────────────── PRIVATE ENDPOINTS ───────────────────

    def get_account(self) -> dict:
        data = self._get("/api/v1/private/account/assets", auth=True)
        return data.get("data", [])

    def get_positions(self) -> List[dict]:
        data = self._get("/api/v1/private/position/open_positions", auth=True)
        return data.get("data", []) or []

    def get_open_orders(self, symbol: str = None) -> List[dict]:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/api/v1/private/order/list/open_orders/", params=params, auth=True)
        return data.get("data", {}).get("resultList", []) or []

    def place_order(self, symbol: str, side: int, vol: float,
                    order_type: int = 5, price: float = None,
                    leverage: int = 10, open_type: int = 1,
                    stop_loss_price: float = None,
                    take_profit_price: float = None) -> dict:
        """
        Place a futures order.
        side: 1=Open Long, 2=Close Short, 3=Open Short, 4=Close Long
        type: 5=Market, 1=Limit
        open_type: 1=Isolated, 2=Cross
        """
        if self.dry_run:
            log.info(f"[DRY-RUN] place_order symbol={symbol} side={side} vol={vol} slp={stop_loss_price} tp={take_profit_price}")
            return {"data": f"DRY_{int(time.time())}", "success": True}

        body = {
            "symbol": symbol,
            "side": side,
            "openType": open_type,
            "type": order_type,
            "vol": vol,
            "leverage": leverage,
        }
        if price and order_type == 1:
            body["price"] = price
        if stop_loss_price:
            body["stopLossPrice"] = stop_loss_price
        if take_profit_price:
            body["takeProfitPrice"] = take_profit_price

        data = self._post("/api/v1/private/order/submit", body)
        if data.get("success"):
            log.info(f"Order placed | symbol={symbol} side={side} vol={vol} orderId={data.get('data')}")
        else:
            log.error(f"Order failed | {data}")
        return data

    def cancel_order(self, order_id: str) -> dict:
        if self.dry_run:
            log.info(f"[DRY-RUN] cancel_order {order_id}")
            return {"success": True}
        return self._post("/api/v1/private/order/cancel", {"orderId": order_id})

    def set_leverage(self, symbol: str, leverage: int, position_type: int = 1) -> dict:
        """position_type: 1=Long, 2=Short"""
        return self._post("/api/v1/private/position/change_leverage", {
            "symbol": symbol,
            "leverage": leverage,
            "openType": 1,
            "positionType": position_type,
        })

    def test_connection(self) -> bool:
        """Test API connectivity and authentication."""
        log.info("Testing MEXC connection...")
        try:
            # Public test
            ticker = self.get_ticker("BTC_USDT")
            if ticker:
                log.info(f"✓ Public API OK | BTC price: {ticker.get('lastPrice')}")
            else:
                log.error("✗ Public API failed")
                return False

            # Auth test
            if self.api_key != "your_api_key_here":
                assets = self.get_account()
                if assets is not None:
                    log.info(f"✓ Private API OK | Got account data")
                    return True
                else:
                    log.error("✗ Private API failed - check your API keys")
                    return False
            else:
                log.warning("API keys not configured – only public API tested")
                return True
        except Exception as e:
            log.error(f"Connection test failed: {e}")
            return False

    # ─────────────────────── WEBSOCKET ───────────────────────────

    def subscribe_ws(self, symbols: List[str], on_kline: Callable = None, on_ticker: Callable = None):
        """Start WebSocket and subscribe to kline + ticker channels."""
        ws_url = "wss://contract.mexc.com/edge"

        def on_open(ws):
            log.info("WebSocket connected")
            self._ws_connected.set()
            for sym in symbols:
                sub_kline = json.dumps({"method": "sub.kline", "param": {"symbol": sym, "interval": "Min15"}})
                sub_ticker = json.dumps({"method": "sub.ticker", "param": {"symbol": sym}})
                ws.send(sub_kline)
                ws.send(sub_ticker)
                log.info(f"Subscribed to {sym}")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                channel = data.get("channel", "")
                if "kline" in channel and on_kline:
                    on_kline(data)
                elif "ticker" in channel and on_ticker:
                    on_ticker(data)
            except Exception as e:
                log.debug(f"WS message parse error: {e}")

        def on_error(ws, error):
            log.error(f"WebSocket error: {error}")

        def on_close(ws, code, msg):
            log.warning(f"WebSocket closed: {code} {msg}")
            self._ws_connected.clear()
            time.sleep(5)
            log.info("Reconnecting WebSocket...")
            self.subscribe_ws(symbols, on_kline, on_ticker)

        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={"ping_interval": 20, "ping_timeout": 10},
            daemon=True
        )
        self._ws_thread.start()
        self._ws_connected.wait(timeout=15)
        log.info("WebSocket ready")
