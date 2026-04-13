import asyncio
import json
import websockets
from decimal import Decimal
import time

# --- INSTITUTIONAL CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "BINANCE_FEE": Decimal("0.001"), # 0.1% Taker
    "BYBIT_FEE": Decimal("0.001"),   # 0.1% Taker
    "MIN_SPREAD_THRESHOLD": Decimal("0.0035"), # 0.35% (Covers fees + slippage)
    "SLIPPAGE_PROTECTION": Decimal("0.0005"),  # 0.05% safety buffer
}

class LeadLagArb:
    def __init__(self):
        self.prices = {"binance": Decimal("0"), "bybit": Decimal("0")}
        self.balance = Decimal("2000.00") # $1000 on each exchange

    async def stream_binance(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@ticker"
        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                self.prices["binance"] = Decimal(data['a']) # Best Ask

    async def stream_bybit(self):
        # Using public Bybit websocket
        url = "wss://stream.bybit.com/v5/public/spot"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{CONFIG['SYMBOL'].upper()}"]}))
            while True:
                data = json.loads(await ws.recv())
                if 'data' in data:
                    self.prices["bybit"] = Decimal(data['data']['ask1P'])

    async def trade_engine(self):
        print("⚔️ ARBITRAGE ENGINE ONLINE | Monitoring Lead-Lag...")
        while True:
            b_p = self.prices["binance"]
            by_p = self.prices["bybit"]

            if b_p > 0 and by_p > 0:
                # Calculate Raw Spread
                # If Binance (Leader) jumps, Bybit (Laggard) is still cheap
                spread = (b_p - by_p) / by_p
                
                # TOTAL COST = (Binance Fee + Bybit Fee + Slippage Buffer)
                total_costs = CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"] + CONFIG["SLIPPAGE_PROTECTION"]

                if spread > CONFIG["MIN_SPREAD_THRESHOLD"]:
                    net_profit = spread - total_costs
                    profit_usd = (Decimal("100.0") * net_profit)
                    
                    print(f"\n🚀 ARB OPPORTUNITY FOUND!")
                    print(f"LDR (Binance): {b_p} | LAG (Bybit): {by_p}")
                    print(f"Spread: {round(spread*100, 3)}% | NET: ${round(profit_usd, 2)}")
                    
                    self.balance += profit_usd
                    # Cooldown to let laggard catch up
                    await asyncio.sleep(2)

            await asyncio.sleep(0.01) # 10ms check loop

    async def run(self):
        await asyncio.gather(
            self.stream_binance(),
            self.stream_bybit(),
            self.trade_engine()
        )

if __name__ == "__main__":
    asyncio.run(LeadLagArb().run())
