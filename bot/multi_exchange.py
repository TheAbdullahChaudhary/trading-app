"""
Multi-Exchange Data Aggregator
Gets data from Binance, Coinbase, Kraken BEFORE it reaches MEXC
Predicts MEXC price moves with 1-5 second advantage
"""
import asyncio
import websockets
import json
import time
from collections import deque
import logging

log = logging.getLogger(__name__)


class MultiExchangeAggregator:
    """Aggregate data from multiple exchanges to predict MEXC moves"""
    
    def __init__(self):
        self.prices = {
            'binance': {},
            'coinbase': {},
            'kraken': {},
            'mexc': {}
        }
        self.price_history = {}
        self.signals = {}
        self.running = False
        
    async def connect_binance(self, symbols):
        """Connect to Binance WebSocket"""
        # Map MEXC symbols to Binance
        symbol_map = {
            'BTC_USDT': 'btcusdt',
            'ETH_USDT': 'ethusdt',
            'XAUT_USDT': 'xautusdt'
        }
        
        streams = [f"{symbol_map.get(s, s.lower().replace('_', ''))}@trade" 
                   for s in symbols if s in symbol_map]
        
        if not streams:
            return
            
        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
        
        try:
            async with websockets.connect(url) as ws:
                log.info("✓ Binance WebSocket connected")
                while self.running:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    data = json.loads(msg)
                    
                    if 'data' in data:
                        symbol = data['data']['s'].upper()
                        price = float(data['data']['p'])
                        
                        # Convert back to MEXC format
                        for mexc_sym, binance_sym in symbol_map.items():
                            if binance_sym == symbol.lower():
                                self.prices['binance'][mexc_sym] = price
                                self._analyze_arbitrage(mexc_sym)
                                break
        except Exception as e:
            log.error(f"Binance WebSocket error: {e}")
    
    async def connect_coinbase(self, symbols):
        """Connect to Coinbase WebSocket"""
        symbol_map = {
            'BTC_USDT': 'BTC-USD',
            'ETH_USDT': 'ETH-USD'
        }
        
        products = [symbol_map.get(s) for s in symbols if s in symbol_map]
        if not products:
            return
        
        url = "wss://ws-feed.exchange.coinbase.com"
        
        try:
            async with websockets.connect(url) as ws:
                # Subscribe
                subscribe_msg = {
                    "type": "subscribe",
                    "product_ids": products,
                    "channels": ["ticker"]
                }
                await ws.send(json.dumps(subscribe_msg))
                log.info("✓ Coinbase WebSocket connected")
                
                while self.running:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    data = json.loads(msg)
                    
                    if data.get('type') == 'ticker':
                        product = data['product_id']
                        price = float(data['price'])
                        
                        # Convert to MEXC format
                        for mexc_sym, cb_sym in symbol_map.items():
                            if cb_sym == product:
                                self.prices['coinbase'][mexc_sym] = price
                                self._analyze_arbitrage(mexc_sym)
                                break
        except Exception as e:
            log.error(f"Coinbase WebSocket error: {e}")
    
    def update_mexc_price(self, symbol: str, price: float):
        """Update MEXC price for comparison"""
        self.prices['mexc'][symbol] = price
        self._analyze_arbitrage(symbol)
    
    def _analyze_arbitrage(self, symbol: str):
        """
        Analyze price differences between exchanges
        Generate early signal if other exchanges moved but MEXC hasn't yet
        """
        if symbol not in self.price_history:
            self.price_history[symbol] = deque(maxlen=20)
        
        # Get prices from all exchanges
        binance_price = self.prices['binance'].get(symbol)
        coinbase_price = self.prices['coinbase'].get(symbol)
        mexc_price = self.prices['mexc'].get(symbol)
        
        if not mexc_price:
            return
        
        # Calculate average of external exchanges
        external_prices = []
        if binance_price:
            external_prices.append(binance_price)
        if coinbase_price:
            external_prices.append(coinbase_price)
        
        if not external_prices:
            return
        
        avg_external = sum(external_prices) / len(external_prices)
        
        # Calculate price difference (arbitrage opportunity)
        diff_pct = (avg_external - mexc_price) / mexc_price * 100
        
        # Store history
        self.price_history[symbol].append({
            'time': time.time(),
            'mexc': mexc_price,
            'external': avg_external,
            'diff': diff_pct
        })
        
        # Generate signal if significant difference
        signal = self._generate_arbitrage_signal(symbol, diff_pct)
        
        if signal:
            self.signals[symbol] = signal
            log.info(f"{symbol}: ARBITRAGE signal {signal['direction']} "
                    f"diff={diff_pct:.3f}% | Binance={binance_price} MEXC={mexc_price}")
    
    def _generate_arbitrage_signal(self, symbol: str, diff_pct: float) -> dict:
        """
        Generate trading signal based on arbitrage
        
        Logic: If Binance/Coinbase price > MEXC, expect MEXC to rise (BUY)
               If Binance/Coinbase price < MEXC, expect MEXC to fall (SELL)
        """
        # Thresholds
        STRONG_THRESHOLD = 0.05  # 0.05% difference = strong signal
        WEAK_THRESHOLD = 0.02    # 0.02% difference = weak signal
        
        if abs(diff_pct) < WEAK_THRESHOLD:
            return None
        
        direction = 'BUY' if diff_pct > 0 else 'SELL'
        strength = min(abs(diff_pct) * 1000, 100)  # Scale to 0-100
        
        # Check if trend is consistent
        if len(self.price_history[symbol]) >= 5:
            recent_diffs = [h['diff'] for h in list(self.price_history[symbol])[-5:]]
            consistent = all(d > 0 for d in recent_diffs) or all(d < 0 for d in recent_diffs)
            
            if consistent:
                strength = min(strength * 1.2, 100)  # Boost if consistent
        
        return {
            'direction': direction,
            'strength': strength,
            'diff_pct': diff_pct,
            'reason': f"Arbitrage: External exchanges {diff_pct:+.3f}% vs MEXC",
            'type': 'ARBITRAGE'
        }
    
    def get_signal(self, symbol: str) -> dict:
        """Get latest arbitrage signal for symbol"""
        signal = self.signals.get(symbol)
        
        if not signal:
            return {'direction': 'HOLD', 'strength': 0, 'type': 'NONE'}
        
        # Check if signal is still fresh (< 10 seconds old)
        if symbol in self.price_history and len(self.price_history[symbol]) > 0:
            last_update = self.price_history[symbol][-1]['time']
            if time.time() - last_update > 10:
                return {'direction': 'HOLD', 'strength': 0, 'type': 'STALE'}
        
        return signal
    
    async def start(self, symbols):
        """Start all exchange connections"""
        self.running = True
        
        tasks = [
            self.connect_binance(symbols),
            self.connect_coinbase(symbols)
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    def stop(self):
        """Stop all connections"""
        self.running = False
