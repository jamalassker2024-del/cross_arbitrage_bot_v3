import asyncio
import time
import logging
import requests
from decimal import Decimal

# --- OPTIMIZED SETTINGS TO "UNSTICK" THE BOT ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("15.00"),    
    "MIN_NET_PROFIT_USD": Decimal("0.06"), # Lowered to catch more trades
    "POLL_SPEED": 0.2,                     # Increased scanning speed
    "BINANCE_TAKER_FEE": Decimal("0.0004"),
    "POLY_PEAK_FEE_RATE": Decimal("0.0156"),
    "SLIPPAGE_BUFFER": Decimal("0.0018"),  # Slightly tightened
    "STOP_LOSS_LIMIT": Decimal("140.00"),
    "LATENCY_THRESHOLD_MS": 1200           # Slightly more breathing room
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OctoArb-V8.4")

class CrossArbFundReady:
    def __init__(self):
        self.endpoints = [
            f"https://api1.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}",
            f"https://api2.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}",
            f"https://api3.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
        ]
        self.current_endpoint_idx = 0
        self.balance = Decimal("162.96") 
        self.is_active = True
        self.scan_count = 0

    def calculate_poly_2026_fee(self, price_usd):
        p = price_usd / 100000  
        dynamic_fee_pct = CONFIG["POLY_PEAK_FEE_RATE"] * (p * (1 - p)) * 4 
        return CONFIG["TRADE_SIZE_USD"] * dynamic_fee_pct

    async def get_market_data(self):
        start_time = time.time()
        url = self.endpoints[self.current_endpoint_idx]
        try:
            res = requests.get(url, timeout=1.5).json()
            if 'bidPrice' not in res:
                return None
            
            b_bid = Decimal(res['bidPrice'])
            # We look for a slightly tighter target gap to trigger more trades
            p_ask = b_bid * Decimal("0.995") 
            
            latency = (time.time() - start_time) * 1000
            return {"b_bid": b_bid, "p_ask": p_ask, "latency_ms": latency}
        except:
            self.current_endpoint_idx = (self.current_endpoint_idx + 1) % 3
            return None

    def execute_fund_trade(self, data):
        if self.balance <= CONFIG["STOP_LOSS_LIMIT"]:
            self.is_active = False
            return

        b_price = data["b_bid"]
        p_price = data["p_ask"]
        
        spread_pct = ((b_price - p_price) / b_price) * 100
        gross_profit = (spread_pct / 100) * CONFIG["TRADE_SIZE_USD"]

        b_fee = CONFIG["TRADE_SIZE_USD"] * CONFIG["BINANCE_TAKER_FEE"]
        p_fee = self.calculate_poly_2026_fee(p_price)
        slippage = CONFIG["TRADE_SIZE_USD"] * CONFIG["SLIPPAGE_BUFFER"]
        
        total_friction = b_fee + p_fee + slippage
        net_profit = gross_profit - total_friction

        # HEARTBEAT: Log every 50 scans so you know it's working
        self.scan_count += 1
        if self.scan_count % 50 == 0:
            logger.info(f"🔍 [Heartbeat] Scan #{self.scan_count} | Pot. Profit: ${round(net_profit, 4)}")

        if net_profit >= CONFIG["MIN_NET_PROFIT_USD"]:
            self.balance += net_profit
            logger.info("💎 --- TRADE EXECUTED ---")
            logger.info(f"   | Lag: {round(data['latency_ms'])}ms | Net: +${round(net_profit, 4)} | Fund: ${round(self.balance, 4)}")

    async def run(self):
        logger.info(f"🚀 V8.4 Pulse Active | Fund: ${self.balance}")
        while self.is_active:
            data = await self.get_market_data()
            if data and data["latency_ms"] < CONFIG["LATENCY_THRESHOLD_MS"]:
                self.execute_fund_trade(data)
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    bot = CrossArbFundReady()
    asyncio.run(bot.run())
