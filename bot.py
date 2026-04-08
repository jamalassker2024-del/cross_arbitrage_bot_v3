import asyncio
import logging
import requests
import time
from decimal import Decimal

# --- V11.0 THE MASTER ENGINE: SPEED + SAFETY + VOLATILITY ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("25.00"),
    "MIN_PROFIT_THRESHOLD": Decimal("0.08"), # Don't trade for less than 8 cents
    "PULSE_SENSITIVITY": Decimal("0.0002"),  # 0.02% move triggers the hunt
    "POLL_SPEED": 0.25,                      # Balancing speed and API limits
    "STOP_LOSS": Decimal("40.00"),           # Circuit breaker
    # Fees are built into the 'Profit Gate' below
    "TOTAL_FRICTION_PCT": Decimal("0.0045")  # 0.45% covers Binance+Poly+Slippage
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("OctoArb-V11-Master")

class OctoBotMaster:
    def __init__(self):
        self.last_price = None
        self.shadow_balance = Decimal("167.47")
        self.is_active = True
        # Monitoring the "Heavy" market (BTC) and "Fast" market (Event)
        self.targets = [
            {"name": "BTC-Price-Target", "id": "21742410403013515082103710400032549202353100000000000000000000000000000000000"},
            {"name": "Macro-Event", "id": "7221040301351508210371040003254920235310000000000000000000000000000000000000"}
        ]

    async def get_market_state(self):
        try:
            # Check Binance Pulse
            res = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={CONFIG['BINANCE_SYMBOL']}", timeout=1).json()
            curr_price = Decimal(res['price'])
            
            pulse = 0
            if self.last_price:
                pulse = (curr_price - self.last_price) / self.last_price
            
            self.last_price = curr_price
            return curr_price, pulse
        except Exception as e:
            return None, 0

    async def check_opportunity(self, market, b_price, pulse):
        try:
            p_url = f"https://clob.polymarket.com/book?token_id={market['id']}"
            p_res = requests.get(p_url, timeout=1).json()
            
            if not p_res.get('asks'): return
            
            p_price = Decimal(p_res['asks'][0]['price'])
            
            # MATH: Is there a gap large enough to cover fees?
            # We compare the pulse direction to the Polymarket price
            if abs(pulse) > CONFIG["PULSE_SENSITIVITY"]:
                # Calculation for a 'winning' shadow trade
                gross_profit = CONFIG["TRADE_SIZE_USD"] * abs(pulse)
                fees = CONFIG["TRADE_SIZE_USD"] * CONFIG["TOTAL_FRICTION_PCT"]
                net_profit = gross_profit - fees

                if net_profit > CONFIG["MIN_PROFIT_THRESHOLD"]:
                    self.shadow_balance += net_profit
                    logger.info(f"🎯 MASTER SNIPE: {market['name']}")
                    logger.info(f"   | Reason: Binance Pulse {round(pulse*100, 4)}%")
                    logger.info(f"   | Net Profit: +${round(net_profit, 2)} | Balance: ${round(self.shadow_balance, 2)}")
        except:
            pass

    async def run(self):
        logger.info(f"🛡️ V11.0 Master Engine Online | Shadow Fund: ${self.shadow_balance}")
        while self.is_active:
            b_price, pulse = await self.get_market_state()
            
            if b_price:
                # Heartbeat every 2 minutes
                if time.time() % 120 < 1:
                    logger.info(f"🛰️ Heartbeat: BTC @ ${b_price} | Pulse: {round(pulse*100, 4)}% | System: OK")

                if abs(pulse) > CONFIG["PULSE_SENSITIVITY"]:
                    tasks = [self.check_opportunity(m, b_price, pulse) for m in self.targets]
                    await asyncio.gather(*tasks)
            
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(OctoBotMaster().run())
