import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time
import sys

getcontext().prec = 12

CONFIG = {
    # Using a slightly smaller list to ensure we don't hit URL length limits
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT", "SUIUSDT"],
    "BINANCE_FEE": Decimal("0.001"),
    "BYBIT_FEE": Decimal("0.001"),
    "THRESHOLD": Decimal("0.0012"), # 0.12%
    "ORDER_SIZE": Decimal("50"),
}

class ArbitrageV4:
    def __init__(self):
        # Initializing prices with None to distinguish between "No data" and "Price is 0"
        self.prices = {s: {"bin_a": None, "bin_b": None, "byb_a": None, "byb_b": None} for s in CONFIG["SYMBOLS"]}
        self.running = True

    async def stream_binance(self):
        # CORRECT URL FORMAT: wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker
        streams = "/".join([f"{s.lower()}@bookTicker" for s in CONFIG["SYMBOLS"]])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    print("✅ Binance Stream Connected")
                    while self.running:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        if 'data' in data:
                            d = data['data']
                            s = d['s']
                            self.prices[s]["bin_a"] = Decimal(str(d['a']))
                            self.prices[s]["bin_b"] = Decimal(str(d['b']))
            except Exception as e:
                print(f"❌ Binance Error: {e}. Retrying...")
                await asyncio.sleep(2)

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    print("✅ Bybit Stream Connected")
                    sub_msg = {"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}
                    await ws.send(json.dumps(sub_msg))
                    while self.running:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        if 'data' in data:
                            d = data['data']
                            # Handle both Snapshot (list) and Delta (dict)
                            items = d if isinstance(d, list) else [d]
                            for item in items:
                                s = item.get('s')
                                if s in self.prices:
                                    if 'ask1Price' in item and item['ask1Price']:
                                        self.prices[s]["byb_a"] = Decimal(str(item['ask1Price']))
                                    if 'bid1Price' in item and item['bid1Price']:
                                        self.prices[s]["byb_b"] = Decimal(str(item['bid1Price']))
            except Exception as e:
                print(f"❌ Bybit Error: {e}. Retrying...")
                await asyncio.sleep(2)

    async def engine(self):
        print("🚀 SCALPER ENGINE ACTIVE")
        last_log = 0
        while self.running:
            for s in CONFIG["SYMBOLS"]:
                p = self.prices[s]
                
                # Check if we have data from both exchanges
                if all(v is not None for v in p.values()):
                    # Direction 1: Buy Binance, Sell Bybit
                    spread1 = (p["byb_b"] - p["bin_a"]) / p["bin_a"]
                    # Direction 2: Buy Bybit, Sell Binance
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
        print(f"\n🔥 TRADE TRIGGERED | {sym} | {direction} | Spread: {round(spread*100,3)}% | Est. Profit: ${round(profit,4)}")

    def heartbeat(self):
        ready = sum(1 for s in CONFIG["SYMBOLS"] if all(v is not None for v in self.prices[s].values()))
        sample = CONFIG["SYMBOLS"][0]
        p = self.prices[sample]
        print(f"📡 Status: {ready}/{len(CONFIG['SYMBOLS'])} pairs synced | {sample} Bin: {p['bin_a']} Byb: {p['byb_a']}")

    async def run(self):
        await asyncio.gather(self.stream_binance(), self.stream_bybit(), self.engine())

if __name__ == "__main__":
    try:
        asyncio.run(ArbitrageV4().run())
    except KeyboardInterrupt:
        print("Stopping...")
