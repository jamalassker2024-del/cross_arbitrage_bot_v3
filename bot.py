import asyncio
import json
import websockets
from decimal import Decimal
import time
import sys

# --- AGGRESSIVE CONFIG ---
CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "SUIUSDT", 
                "AVAXUSDT", "APTUSDT", "ARBUSDT", "DOGEUSDT", "NEARUSDT"],
    "BINANCE_FEE": Decimal("0.001"), 
    "BYBIT_FEE": Decimal("0.001"),   
    "MIN_SPREAD_THRESHOLD": Decimal("0.0018"), # 0.18% - Much more aggressive
    "SLIPPAGE_PROTECTION": Decimal("0.0003"),  
    "LOG_INTERVAL": 10
}

class SovereignScalper:
    def __init__(self):
        self.prices = {s: {"binance": Decimal("0"), "bybit": Decimal("0")} for s in CONFIG["SYMBOLS"]}
        self.last_update = {s: {"binance": 0, "bybit": 0} for s in CONFIG["SYMBOLS"]}
        self.balance = Decimal("2000.00")
        self.last_log = 0

    async def stream_binance(self):
        streams = "/".join([f"{s.lower()}@bookTicker" for s in CONFIG["SYMBOLS"]])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                s = data['data']['s']
                self.prices[s]["binance"] = Decimal(data['data']['a'])
                self.last_update[s]["binance"] = time.time()

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        async with websockets.connect(url) as ws:
            args = [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]
            await ws.send(json.dumps({"op": "subscribe", "args": args}))
            while True:
                data = json.loads(await ws.recv())
                if 'data' in data:
                    d = data['data']
                    s = d.get('symbol')
                    if s in self.prices and 'ask1Price' in d:
                        self.prices[s]["bybit"] = Decimal(d['ask1Price'])
                        self.last_update[s]["bybit"] = time.time()

    async def engine(self):
        print(f"⚔️ AGGRESSIVE SCALPER STARTING | {len(CONFIG['SYMBOLS'])} PAIRS")
        max_seen_spread = 0
        
        while True:
            now = time.time()
            for s in CONFIG["SYMBOLS"]:
                b_p = self.prices[s]["binance"]
                by_p = self.prices[s]["bybit"]

                # Relaxed to 5 seconds to handle Railway network lag
                if (now - self.last_update[s]["binance"] < 5) and (now - self.last_update[s]["bybit"] < 5):
                    if b_p > 0 and by_p > 0:
                        spread = (abs(b_p - by_p)) / min(b_p, by_p)
                        
                        if float(spread) > max_seen_spread:
                            max_seen_spread = float(spread)

                        costs = CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"] + CONFIG["SLIPPAGE_PROTECTION"]

                        if spread > CONFIG["MIN_SPREAD_THRESHOLD"]:
                            profit = (Decimal("100.0") * (spread - costs))
                            self.balance += profit
                            print(f"\n🔥 DEAL OPENED: {s} | Spread: {round(spread*100, 3)}% | Net: +${round(profit, 4)}")
                            print(f"💰 BALANCE: ${round(self.balance, 2)}")
                            # Clear update time to prevent double-firing
                            self.last_update[s]["binance"] = 0 

            if now - self.last_log > CONFIG["LOG_INTERVAL"]:
                sys.stdout.write(f"\r📡 SCANNING... Max Spread Seen: {round(max_seen_spread*100, 3)}% | Target: {round(float(CONFIG['MIN_SPREAD_THRESHOLD'])*100, 3)}%   ")
                sys.stdout.flush()
                max_seen_spread = 0
                self.last_log = now
            
            await asyncio.sleep(0.01)

    async def run(self):
        await asyncio.gather(self.stream_binance(), self.stream_bybit(), self.engine())

if __name__ == "__main__":
    asyncio.run(SovereignScalper().run())
