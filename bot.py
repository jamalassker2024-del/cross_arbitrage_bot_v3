import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time
from collections import deque

getcontext().prec = 12

# ========== CONFIGURATION ==========
CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "BINANCE_FEE": Decimal("0.001"),       # 0.1% taker fee
    "BYBIT_FEE": Decimal("0.001"),
    "MARKET_ORDER_THRESHOLD": Decimal("0.0035"),  # 0.35% spread -> use market orders
    "LIMIT_ORDER_THRESHOLD": Decimal("0.0025"),   # 0.25% spread -> use limit orders
    "ORDER_SIZE_USDT": Decimal("100"),
    "SLIPPAGE_MARKET": Decimal("0.0005"),         # 0.05% slippage for market orders
    "MAX_LIMIT_WAIT_SEC": 5,                      # cancel limit orders after 5s
    "LATENCY_MS": 150,                            # simulated network delay (Railway realistic)
}

class RealArbitrageExecutor:
    def __init__(self):
        # Real-time prices
        self.prices = {s: {"binance_ask": Decimal("0"), "binance_bid": Decimal("0"),
                           "bybit_ask": Decimal("0"), "bybit_bid": Decimal("0")}
                       for s in CONFIG["SYMBOLS"]}
        self.open_limit_orders = []   # each: {symbol, side, price, qty, exchange, timestamp, counterpart}
        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "total_profit_usdt": Decimal("0"),
            "total_fees_paid": Decimal("0"),
        }
        self.last_log = 0

    # ---------- WebSocket feeds (real data) ----------
    async def stream_binance(self):
        streams = "/".join([f"{s.lower()}@bookTicker" for s in CONFIG["SYMBOLS"]])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                s = data['data']['s']
                self.prices[s]["binance_ask"] = Decimal(data['data']['a'])
                self.prices[s]["binance_bid"] = Decimal(data['data']['b'])

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}))
            while True:
                data = json.loads(await ws.recv())
                if 'data' in data:
                    d = data['data']
                    s = d.get('symbol')
                    if s in self.prices:
                        self.prices[s]["bybit_ask"] = Decimal(d['ask1Price'])
                        self.prices[s]["bybit_bid"] = Decimal(d['bid1Price'])

    # ---------- Order execution logic (simulated but realistic) ----------
    async def execute_market_arbitrage(self, symbol, buy_exch, sell_exch, buy_price, sell_price):
        """Immediate market order execution on both legs."""
        # Simulate latency + slippage
        await asyncio.sleep(CONFIG["LATENCY_MS"] / 1000.0)
        # Re-check prices after latency
        if buy_exch == "binance":
            new_buy_price = self.prices[symbol]["binance_ask"]
        else:
            new_buy_price = self.prices[symbol]["bybit_ask"]
        if sell_exch == "binance":
            new_sell_price = self.prices[symbol]["binance_bid"]
        else:
            new_sell_price = self.prices[symbol]["bybit_bid"]

        # Apply slippage buffer (worst-case execution)
        buy_price_exec = new_buy_price * (1 + CONFIG["SLIPPAGE_MARKET"])
        sell_price_exec = new_sell_price * (1 - CONFIG["SLIPPAGE_MARKET"])

        qty = CONFIG["ORDER_SIZE_USDT"] / buy_price_exec
        gross_return = qty * sell_price_exec
        cost_basis = qty * buy_price_exec
        fee_buy = cost_basis * CONFIG["BINANCE_FEE"]
        fee_sell = gross_return * CONFIG["BYBIT_FEE"]
        net_profit = (gross_return - cost_basis) - fee_buy - fee_sell

        # Record trade
        self.stats["total_trades"] += 1
        self.stats["total_fees_paid"] += fee_buy + fee_sell
        if net_profit > 0:
            self.stats["winning_trades"] += 1
        self.stats["total_profit_usdt"] += net_profit

        win_rate = (self.stats["winning_trades"] / self.stats["total_trades"] * 100) if self.stats["total_trades"] else 0
        print(f"\n🔥 MARKET ARBITRAGE {symbol} | Profit: ${net_profit:.2f} | WinRate: {win_rate:.1f}%")
        return net_profit

    async def place_limit_arbitrage(self, symbol, buy_exch, sell_exch, buy_price, sell_price):
        """Place limit orders (post‑only) – will be filled only if price moves in our favor."""
        qty = CONFIG["ORDER_SIZE_USDT"] / buy_price
        order = {
            "symbol": symbol,
            "buy_exch": buy_exch,
            "sell_exch": sell_exch,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "qty": qty,
            "timestamp": time.time()
        }
        self.open_limit_orders.append(order)
        print(f"📌 LIMIT ORDER PLACED {symbol} | Buy {buy_exch}@{buy_price} | Sell {sell_exch}@{sell_price}")

    async def check_limit_orders(self):
        """Monitor open limit orders – if both legs become fillable, close the trade."""
        still_open = []
        for order in self.open_limit_orders:
            sym = order["symbol"]
            # Check if sell limit would be hit (market bid >= sell_price)
            if order["sell_exch"] == "binance":
                current_bid = self.prices[sym]["binance_bid"]
            else:
                current_bid = self.prices[sym]["bybit_bid"]

            if current_bid >= order["sell_price"]:
                # Simulated fill – both legs assumed filled at their limit prices
                qty = order["qty"]
                gross_return = qty * order["sell_price"]
                cost_basis = qty * order["buy_price"]
                fee_buy = cost_basis * CONFIG["BINANCE_FEE"]
                fee_sell = gross_return * CONFIG["BYBIT_FEE"]
                net_profit = (gross_return - cost_basis) - fee_buy - fee_sell

                self.stats["total_trades"] += 1
                self.stats["total_fees_paid"] += fee_buy + fee_sell
                if net_profit > 0:
                    self.stats["winning_trades"] += 1
                self.stats["total_profit_usdt"] += net_profit
                win_rate = (self.stats["winning_trades"] / self.stats["total_trades"] * 100) if self.stats["total_trades"] else 0
                print(f"\n✅ LIMIT FILLED {sym} | Profit: ${net_profit:.2f} | WinRate: {win_rate:.1f}%")
                continue  # order filled, do not keep open
            elif time.time() - order["timestamp"] > CONFIG["MAX_LIMIT_WAIT_SEC"]:
                print(f"⌛ LIMIT EXPIRED {sym} – no fill within {CONFIG['MAX_LIMIT_WAIT_SEC']}s")
                continue
            else:
                still_open.append(order)
        self.open_limit_orders = still_open

    # ---------- Opportunity scanner (real data) ----------
    async def scan_opportunities(self):
        for sym in CONFIG["SYMBOLS"]:
            b_ask = self.prices[sym]["binance_ask"]
            b_bid = self.prices[sym]["binance_bid"]
            by_ask = self.prices[sym]["bybit_ask"]
            by_bid = self.prices[sym]["bybit_bid"]
            if b_ask == 0 or by_ask == 0:
                continue

            # Determine cheaper ask (buy side) and higher bid (sell side)
            if b_ask < by_ask:
                buy_exch, sell_exch = "binance", "bybit"
                buy_price, sell_price = b_ask, by_bid
            else:
                buy_exch, sell_exch = "bybit", "binance"
                buy_price, sell_price = by_ask, b_bid

            if buy_price == 0 or sell_price == 0:
                continue

            gross_spread = (sell_price - buy_price) / buy_price
            # Decide order type based on spread size
            if gross_spread >= CONFIG["MARKET_ORDER_THRESHOLD"]:
                # Large spread – use market orders for guaranteed fill
                await self.execute_market_arbitrage(sym, buy_exch, sell_exch, buy_price, sell_price)
            elif gross_spread >= CONFIG["LIMIT_ORDER_THRESHOLD"]:
                # Medium spread – try limit orders to avoid slippage
                await self.place_limit_arbitrage(sym, buy_exch, sell_exch, buy_price, sell_price)
            # else: spread too small, ignore

    # ---------- Main loop ----------
    async def run(self):
        print("🚀 REAL ARBITRAGE BOT (Demo Mode) – Using Live Binance & Bybit Data")
        print(f"Market order threshold: {float(CONFIG['MARKET_ORDER_THRESHOLD'])*100:.2f}% | Limit threshold: {float(CONFIG['LIMIT_ORDER_THRESHOLD'])*100:.2f}%")
        asyncio.create_task(self.stream_binance())
        asyncio.create_task(self.stream_bybit())

        while True:
            await self.scan_opportunities()
            await self.check_limit_orders()
            if time.time() - self.last_log > 15:
                win_rate = (self.stats["winning_trades"] / self.stats["total_trades"] * 100) if self.stats["total_trades"] else 0
                print(f"\n📊 STATS | Trades: {self.stats['total_trades']} | Wins: {self.stats['winning_trades']} | WinRate: {win_rate:.1f}% | Net Profit: ${self.stats['total_profit_usdt']:.2f} | Fees: ${self.stats['total_fees_paid']:.2f}")
                self.last_log = time.time()
            await asyncio.sleep(0.05)  # 50ms scan interval

if __name__ == "__main__":
    asyncio.run(RealArbitrageExecutor().run())
