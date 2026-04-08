import asyncio
import time
import logging
import requests
from decimal import Decimal

# --- V9.0 REAL-PRICE SHADOWING (ZERO RISK) ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("20.00"),    
    "MIN_NET_PROFIT_USD": Decimal("0.02"), # The "Success" threshold
    "POLL_SPEED": 0.5,                     # Slowed slightly for API stability
    "BINANCE_TAKER_FEE": Decimal("0.0004"),
    "POLY_PEAK_FEE_RATE": Decimal("0.0156"),
    "SLIPPAGE_BUFFER": Decimal("0.0015"),  # Real-world buffer
    "STOP_LOSS_LIMIT": Decimal("40.00")
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OctoArb-GhostV9")

class RealPriceShadow:
    def __init__(self):
        self.binance_url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
        # Real Polymarket BTC Price Proxy (CLOB API)
        self.poly_url = "https://clob.polymarket.com/book?token_id=21742410403013515082103710400032549202353100000000000000000000000000000000000"
        self.shadow_balance = Decimal("167.47") # Starting where V8.6 left off
        self.is_active = True
        self.scan_count = 0

    def calculate_poly_fee(self, price_usd):
        p = price_usd / 100000  
        fee_pct = CONFIG["POLY_PEAK_FEE_RATE"] * (p * (1 - p)) * 4 
        return CONFIG["TRADE_SIZE_USD"] * fee_pct

    async def get_real_market_data(self):
        try:
            # 1. Get Binance Real Price
            b_res = requests.get(self.binance_url, timeout=2).json()
            b_bid = Decimal(b_res['bidPrice'])

            # 2. Get Polymarket Real Price (Shadowing)
            # If API fails, we simulate a 'tight' market to avoid errors
            p_res = requests.get(self.poly_url, timeout=2).json()
            # Grabbing the best 'Ask' from the order book
            p_ask = Decimal(p_res['asks'][0]['price']) if 'asks' in p_res else b_bid * Decimal("1.0001")
            
            return {"b_bid": b_bid, "p_ask": p_ask}
        except Exception as e:
            logger.error(f"Data Fetch Error: {e}")
            return None

    def analyze_shadow_trade(self, data):
        b_price = data["b_bid"]
        p_price = data["p_ask"]
        
        # Real Math
        spread_pct = ((b_price - p_price) / b_price) * 100
        gross_profit = (spread_pct / 100) * CONFIG["TRADE_SIZE_USD"]

        # Real Friction
        b_fee = CONFIG["TRADE_SIZE_USD"] * CONFIG["BINANCE_TAKER_FEE"]
        p_fee = self.calculate_poly_fee(p_price)
        slippage = CONFIG["TRADE_SIZE_USD"] * CONFIG["SLIPPAGE_BUFFER"]
        
        total_friction = b_fee + p_fee + slippage
        net_profit = gross_profit - total_friction

        self.scan_count += 1
        
        # Log every 10 scans so you can see it's alive
        if self.scan_count % 10 == 0:
            status = "PROFITABLE" if net_profit > 0 else "LOSS-AVOIDED"
            logger.info(f"🛰️ Real-Scan #{self.scan_count} | Net: ${round(net_profit, 4)} | Status: {status}")

        # Shadow Execution (Doesn't touch real money)
        if net_profit >= CONFIG["MIN_NET_PROFIT_USD"]:
            self.shadow_balance += net_profit
            logger.info("👻👻👻 --- SHADOW TRADE (SIMULATED) --- 👻👻👻")
            logger.info(f"   | Real Market Net Profit: +${round(net_profit, 4)}")
            logger.info(f"   | Shadow Fund Balance: ${round(self.shadow_balance, 4)}")
            logger.info(f"   | Real Spread: {round(spread_pct, 4)}%")

    async def run(self):
        logger.info(f"🕵️ V9.0 Shadow Mode Active | Monitoring Real Prices...")
        while self.is_active:
            data = await self.get_real_market_data()
            if data:
                self.analyze_shadow_trade(data)
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    bot = RealPriceShadow()
    asyncio.run(bot.run())
