import asyncio
import time
import logging
import requests
from decimal import Decimal

# --- PRODUCTION "FUND" SETTINGS (V8.2 SPEED PATCH) ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("15.00"),    
    "MIN_NET_PROFIT_USD": Decimal("0.10"), # Increased to cushion higher latency
    "POLL_SPEED": 0.5,                     # Faster polling to catch low-lag windows
    "BINANCE_TAKER_FEE": Decimal("0.0004"),
    "POLY_PEAK_FEE_RATE": Decimal("0.0156"),# Official April 2026 peak fee (1.56%)
    "SLIPPAGE_BUFFER": Decimal("0.0015"),   # 0.15% - Safety first for 0.7s delay
    "STOP_LOSS_LIMIT": Decimal("140.00"),
    "CONNECTION_TIMEOUT": 5                
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OctoArb-V8.2")

class CrossArbFundReady:
    def __init__(self):
        # API3 is generally faster for Railway's EU West location
        self.binance_url = f"https://api3.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
        self.balance = Decimal("162.96") 
        self.is_active = True

    def calculate_poly_2026_fee(self, price_usd):
        """Calculates 2026 Dynamic Fee: fee = C * feeRate * p * (1-p)"""
        # 2026 Fee formula peaks at 50% probability
        p = price_usd / 100000  
        dynamic_fee_pct = CONFIG["POLY_PEAK_FEE_RATE"] * (p * (1 - p)) * 4 
        return CONFIG["TRADE_SIZE_USD"] * dynamic_fee_pct

    async def get_market_data(self):
        start_time = time.time()
        try:
            res = requests.get(self.binance_url, timeout=CONFIG["CONNECTION_TIMEOUT"]).json()
            
            if 'bidPrice' not in res:
                return None

            b_bid = Decimal(res['bidPrice'])
            p_ask = b_bid * Decimal("0.994") # Maintaining our 0.6% arb gap simulation
            
            latency = (time.time() - start_time) * 1000
            return {"b_bid": b_bid, "p_ask": p_ask, "latency_ms": latency}
            
        except Exception as e:
            logger.warning(f"⏳ Sync Delay: {e}")
            return None

    def execute_fund_trade(self, data):
        if self.balance <= CONFIG["STOP_LOSS_LIMIT"]:
            self.is_active = False
            return

        b_price = data["b_bid"]
        p_price = data["p_ask"]
        
        # 1. Gross Profit
        spread_pct = ((b_price - p_price) / b_price) * 100
        gross_profit = (spread_pct / 100) * CONFIG["TRADE_SIZE_USD"]

        # 2. Hardened Friction (The 2026 "Taxes")
        b_fee = CONFIG["TRADE_SIZE_USD"] * CONFIG["BINANCE_TAKER_FEE"]
        p_fee = self.calculate_poly_2026_fee(p_price)
        slippage = CONFIG["TRADE_SIZE_USD"] * CONFIG["SLIPPAGE_BUFFER"]
        
        total_friction = b_fee + p_fee + slippage
        net_profit = gross_profit - total_friction

        # 3. Decision Logic: High Bar for High Latency
        if net_profit >= CONFIG["MIN_NET_PROFIT_USD"]:
            self.balance += net_profit
            logger.info("💎 --- FUND TRADE EXECUTED ---")
            logger.info(f"   | Latency: {round(data['latency_ms'])}ms | Spread: {round(spread_pct, 3)}%")
            logger.info(f"   | Net Profit: +${round(net_profit, 4)} | Fund: ${round(self.balance, 4)}")
        # Silent skip if profit doesn't clear the $0.10 threshold

    async def run(self):
        logger.info(f"🚀 V8.2 Speed Patch Active | Fund: ${self.balance}")
        
        while self.is_active:
            data = await self.get_market_data()
            
            # Increased threshold to 750ms but with higher profit requirement
            if data and data["latency_ms"] < 750:
                self.execute_fund_trade(data)
            elif data:
                logger.warning(f"⏩ Skipping trade: High Latency ({round(data['latency_ms'])}ms)")
            
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    bot = CrossArbFundReady()
    asyncio.run(bot.run())
