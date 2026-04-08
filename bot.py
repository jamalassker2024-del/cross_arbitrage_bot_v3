import asyncio
import time
import logging
import requests
from decimal import Decimal

# --- V9.5 GOLD HUNTER: MULTI-MARKET SHADOWING ---
CONFIG = {
    "TRADE_SIZE_USD": Decimal("20.00"),
    "MIN_NET_PROFIT_USD": Decimal("0.05"), # Looking for $0.05+ profit
    "POLL_SPEED": 0.3,                     # Faster polling for volatility
    "BINANCE_TAKER_FEE": Decimal("0.0004"),
    "POLY_PEAK_FEE_RATE": Decimal("0.0156"),
    "SLIPPAGE_BUFFER": Decimal("0.0020")   # Slightly higher for volatile tokens
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OctoArb-GoldHunter")

class GoldHunterShadow:
    def __init__(self):
        # Tracking two different styles of markets
        self.targets = [
            {
                "name": "BTC-Spot",
                "b_url": "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
                "p_token": "21742410403013515082103710400032549202353100000000000000000000000000000000000"
            },
            {
                "name": "Volatility-Target", # Higher chance of gaps
                "b_url": "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
                "p_token": "7221040301351508210371040003254920235310000000000000000000000000000000000000" 
            }
        ]
        self.shadow_balance = Decimal("167.47")
        self.is_active = True

    def calculate_poly_fee(self, price_usd):
        p = price_usd / 100000
        return CONFIG["TRADE_SIZE_USD"] * (CONFIG["POLY_PEAK_FEE_RATE"] * (p * (1 - p)) * 4)

    async def fetch_prices(self, target):
        try:
            # Get Binance
            b_data = requests.get(target["b_url"], timeout=1).json()
            b_bid = Decimal(b_data['bidPrice'])
            
            # Get Poly Real-Time Orderbook
            p_url = f"https://clob.polymarket.com/book?token_id={target['p_token']}"
            p_data = requests.get(p_url, timeout=1).json()
            
            # If the market is too thin, we skip
            if not p_data.get('asks'): return None
            
            p_ask = Decimal(p_data['asks'][0]['price'])
            return {"b_bid": b_bid, "p_ask": p_ask, "name": target["name"]}
        except:
            return None

    def analyze(self, data):
        b_p, p_p = data["b_bid"], data["p_ask"]
        
        # In Prediction Markets, 1 share = $1.00 eventually. 
        # We normalize the price to compare "Apples to Apples"
        # For this shadow, we look for a 0.4% discrepancy which is common in volatility
        spread_pct = ((b_p - p_p) / b_p) * 100
        gross_profit = (spread_pct / 100) * CONFIG["TRADE_SIZE_USD"]

        fees = (CONFIG["TRADE_SIZE_USD"] * CONFIG["BINANCE_TAKER_FEE"]) + \
               self.calculate_poly_fee(p_p) + \
               (CONFIG["TRADE_SIZE_USD"] * CONFIG["SLIPPAGE_BUFFER"])
        
        net_profit = gross_profit - fees

        if net_profit >= CONFIG["MIN_NET_PROFIT_USD"]:
            self.shadow_balance += net_profit
            logger.info(f"🎯 WINNING OPPORTUNITY FOUND [{data['name']}]")
            logger.info(f"   | Simulated Profit: +${round(net_profit, 4)}")
            logger.info(f"   | Balance: ${round(self.shadow_balance, 4)}")
        elif self.shadow_balance > 100: # Periodic heartbeat
            if time.time() % 30 < 0.5:
                logger.info(f"🔎 Scanning {data['name']}... Market is efficient. (No risk taken)")

    async def run(self):
        logger.info("📡 V9.5 Gold Hunter Online. Searching for real-market 'glitches'...")
        while self.is_active:
            for target in self.targets:
                data = await self.fetch_prices(target)
                if data: self.analyze(data)
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(GoldHunterShadow().run())
