import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time
import sys

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT", "SUIUSDT"],
    "BINANCE_FEE": Decimal("0.001"),
    "BYBIT_FEE": Decimal("0.001"),
    "THRESHOLD": Decimal("0.0012"), 
    "ORDER_SIZE": Decimal("50"),
}

class ArbitrageV5:
    def __init__(self):
        self.prices = {s: {"bin_a": None, "bin_b": None, "byb_a": None, "byb_b": None} for s in CONFIG["SYMBOLS"]}
        self.running = True

    async def binance_pair_stream(self, symbol):
        """Individual stream per pair to avoid HTTP 400 malformed URL errors."""
        url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@bookTicker"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"✅ Binance {symbol} Connected")
                    while self.running:
                        data = json.loads(await ws.recv())
                        self.prices[symbol]["bin_a"] = Decimal(str(data['a']))
                        self.prices[symbol]["bin_b"] = Decimal(str(data['b']))
            except Exception as e:
                print(f"⚠️ Binance {symbol} retry: {e}")
                await asyncio.sleep(5)

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    print("✅ Bybit Spot Connected")
                    sub_msg = {"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}
                    await ws.send(json.dumps(sub_msg))
                    while self.running:
                        data = json.loads(await ws.recv())
                        if 'data' in data:
                            d = data['data']
                            items = d if isinstance(d, list) else [d]
                            for item in items:
                                s = item.get('s')
                                if s in self.prices:
                                    if 'ask1Price' in item and item['ask1Price']:
                                        self.prices[s]["byb_a"] = Decimal(str(item['ask1Price']))
                                    if 'bid1Price' in item and item['bid1Price']:
                                        self.prices[s]["byb_b"] = Decimal(str(item['bid1Price']))
            except Exception as e:
                print(f"⚠️ Bybit error: {e}")
                await asyncio.sleep(5)

    async def engine(self):
        print("🚀 ENGINE STARTING...")
        last_log = 0
        while self.running:
            for s in CONFIG["SYMBOLS"]:
                p = self.prices[s]
                if all(v is not None for v in p.values()):
                    # Binance cheaper (Buy Bin, Sell Byb)
                    spread1 = (p["byb_b"] - p["bin_a"]) / p["bin_a"]
                    # Bybit cheaper (Buy Byb, Sell Bin)
                    spread2 = (p["bin_b"] - p["byb_a"]) / p["byb_a"]

                    if spread1 > CONFIG["THRESHOLD"]:
                        self.log_trade(s, "BIN->BYB", spread1)
                    elif spread2 > CONFIG["THRESHOLD"]:
                        self.log_trade(s, "BYB->BIN", spread2)

            if time.time() - last_log > 10:
                self.heartbeat()
                last_log = time.time()
            await asyncio.sleep(0.01)

    def log_trade(self, sym, direction, spread):
        profit = CONFIG["ORDER_SIZE"] * (spread - (CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"]))
        print(f"\n🔥 {direction} | {sym} | Spread: {round(spread*100,3)}% | Profit: ${round(profit,4)}")

    def heartbeat(self):
        synced = sum(1 for s in CONFIG["SYMBOLS"] if all(v is not None for v in self.prices[s].values()))
        print(f"📡 Status: {synced}/{len(CONFIG['SYMBOLS'])} pairs active | BTC Ask: Bin={self.prices['BTCUSDT']['bin_a']} Byb={self.prices['BTCUSDT']['byb_a']}")

    async def run(self):
        # Create separate tasks for each Binance pair
        tasks = [self.binance_pair_stream(s) for s in CONFIG["SYMBOLS"]]
        tasks.append(self.stream_bybit())
        tasks.append(self.engine())
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(ArbitrageV5().run())
    except KeyboardInterrupt:
        print("Shutdown")
