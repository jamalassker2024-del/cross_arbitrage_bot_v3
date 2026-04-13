import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time
import sys
from collections import deque

getcontext().prec = 12

# ========== CONFIGURATION ==========
CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT", "SUIUSDT"],
    "BINANCE_FEE": Decimal("0.001"),          # 0.1% taker fee
    "BYBIT_FEE": Decimal("0.001"),
    "MIN_SPREAD_THRESHOLD": Decimal("0.0020"), # 0.20% minimum spread to open
    "TAKE_PROFIT_BPS": Decimal("10"),          # 10 bps = 0.1% profit target
    "ORDER_SIZE_USDT": Decimal("50"),          # $50 per trade
    "SLIPPAGE_BPS": Decimal("5"),              # 0.05% slippage for market orders
    "MAX_HOLD_SECONDS": 60,                    # close if not profitable after 60s
    "COOLDOWN_SEC": 10,                        # per symbol cooldown after close
    "INITIAL_BALANCE": Decimal("10000"),
}

# ========== GLOBAL STATE ==========
class ArbitrageBot:
    def __init__(self):
        # Real-time prices: binance_ask, binance_bid, bybit_ask, bybit_bid
        self.prices = {s: {"bin_a": None, "bin_b": None, "byb_a": None, "byb_b": None}
                       for s in CONFIG["SYMBOLS"]}
        # Open positions: key = symbol -> dict with entry details
        self.positions = {}
        # Balance tracking
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_fees = Decimal("0")
        self.total_trades = 0
        self.winning_trades = 0
        self.last_trade_time = {}  # per symbol cooldown
        self.running = True
        self.hourly_profit = Decimal("0")
        self.last_hour_reset = time.time()

    # ------------------- WebSocket Feeds -------------------
    async def binance_pair_stream(self, symbol):
        url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@bookTicker"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    while self.running:
                        data = json.loads(await ws.recv())
                        self.prices[symbol]["bin_a"] = Decimal(str(data['a']))
                        self.prices[symbol]["bin_b"] = Decimal(str(data['b']))
            except Exception as e:
                await asyncio.sleep(5)

    async def stream_bybit(self):
        url = "wss://stream.bybit.com/v5/public/spot"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
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
                await asyncio.sleep(5)

    # ------------------- Position Management -------------------
    def open_position(self, symbol, direction, buy_exch, sell_exch, entry_price):
        """Open a new position (simulated market order)."""
        # Apply slippage to entry price
        slippage = CONFIG["SLIPPAGE_BPS"] / Decimal("10000")
        if direction == "buy":
            exec_price = entry_price * (Decimal("1") + slippage)
        else:
            exec_price = entry_price * (Decimal("1") - slippage)

        quantity = CONFIG["ORDER_SIZE_USDT"] / exec_price
        cost = quantity * exec_price
        fee = cost * CONFIG["BINANCE_FEE"]  # approximate taker fee
        if cost + fee > self.balance:
            print(f"⚠️ Insufficient balance for {symbol}: need ${cost+fee:.2f}, have ${self.balance:.2f}")
            return False

        # Deduct cost + fee from balance
        self.balance -= (cost + fee)
        self.total_fees += fee

        # Store position
        self.positions[symbol] = {
            "direction": direction,
            "buy_exch": buy_exch,
            "sell_exch": sell_exch,
            "entry_price": exec_price,
            "quantity": quantity,
            "entry_time": time.time(),
            "target_price": None,
        }
        # Set take-profit price
        if direction == "buy":
            self.positions[symbol]["target_price"] = exec_price * (Decimal("1") + CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))
        else:
            self.positions[symbol]["target_price"] = exec_price * (Decimal("1") - CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))

        print(f"\n📈 OPEN {symbol} | {direction.upper()} {buy_exch} @ {exec_price:.4f} | Qty: {quantity:.6f} | Balance: ${self.balance:.2f}")
        return True

    def close_position(self, symbol, exit_price, reason="TP"):
        """Close an open position and book profit/loss."""
        pos = self.positions.pop(symbol, None)
        if not pos:
            return

        # Apply slippage on exit
        slippage = CONFIG["SLIPPAGE_BPS"] / Decimal("10000")
        if pos["direction"] == "buy":
            exec_exit = exit_price * (Decimal("1") - slippage)  # selling at bid
        else:
            exec_exit = exit_price * (Decimal("1") + slippage)  # buying back at ask

        gross_return = pos["quantity"] * exec_exit
        fee_exit = gross_return * CONFIG["BINANCE_FEE"]
        cost_basis = pos["quantity"] * pos["entry_price"]
        profit = (gross_return - cost_basis) - fee_exit

        # Update balance (add exit proceeds)
        self.balance += gross_return - fee_exit
        self.total_fees += fee_exit

        # Update stats
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.hourly_profit += profit

        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"🎯 CLOSE {symbol} {reason} | Exit @ {exec_exit:.4f} | Profit: ${profit:.4f} | Balance: ${self.balance:.2f} | WinRate: {win_rate:.1f}%")

        # Set cooldown
        self.last_trade_time[symbol] = time.time()

    # ------------------- Arbitrage Logic -------------------
    async def engine(self):
        print("🚀 ARBITRAGE ENGINE STARTED (with real order management)")
        print(f"   Minimum spread: {float(CONFIG['MIN_SPREAD_THRESHOLD'])*100:.2f}%")
        print(f"   Take-profit: {float(CONFIG['TAKE_PROFIT_BPS'])/100:.2f}%")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   Initial balance: ${self.balance}\n")

        last_heartbeat = 0
        while self.running:
            now = time.time()

            # 1. Check and close positions that reached take-profit or expired
            for sym in list(self.positions.keys()):
                pos = self.positions[sym]
                # Get current price on the exchange where we would exit
                if pos["direction"] == "buy":
                    # Long position: exit on sell exchange's bid price
                    exit_exch = pos["sell_exch"]
                    if exit_exch == "binance":
                        current_price = self.prices[sym]["bin_b"]
                    else:
                        current_price = self.prices[sym]["byb_b"]
                else:
                    # Short position: exit on buy exchange's ask price
                    exit_exch = pos["buy_exch"]
                    if exit_exch == "binance":
                        current_price = self.prices[sym]["bin_a"]
                    else:
                        current_price = self.prices[sym]["byb_a"]

                if current_price is None or current_price == 0:
                    continue

                # Check take-profit
                if (pos["direction"] == "buy" and current_price >= pos["target_price"]) or \
                   (pos["direction"] == "sell" and current_price <= pos["target_price"]):
                    self.close_position(sym, current_price, "TAKE_PROFIT")
                # Check timeout
                elif now - pos["entry_time"] > CONFIG["MAX_HOLD_SECONDS"]:
                    self.close_position(sym, current_price, "TIMEOUT")

            # 2. Look for new arbitrage opportunities
            for sym in CONFIG["SYMBOLS"]:
                # Skip if symbol is in cooldown
                if sym in self.last_trade_time and now - self.last_trade_time[sym] < CONFIG["COOLDOWN_SEC"]:
                    continue
                # Skip if already have an open position on this symbol
                if sym in self.positions:
                    continue

                p = self.prices[sym]
                if all(v is not None for v in p.values()):
                    # Binance cheaper (buy Binance, sell Bybit)
                    spread1 = (p["byb_b"] - p["bin_a"]) / p["bin_a"]
                    # Bybit cheaper (buy Bybit, sell Binance)
                    spread2 = (p["bin_b"] - p["byb_a"]) / p["byb_a"]

                    # Calculate net profit after fees (estimated)
                    total_costs = CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"] + (CONFIG["SLIPPAGE_BPS"] / Decimal("10000")) * 2
                    if spread1 > CONFIG["MIN_SPREAD_THRESHOLD"] and spread1 > total_costs:
                        # Buy on Binance, sell on Bybit
                        entry_price = p["bin_a"]
                        if self.open_position(sym, "buy", "binance", "bybit", entry_price):
                            print(f"🔥 ARBITRAGE OPEN {sym} | Spread1: {float(spread1)*100:.3f}%")
                    elif spread2 > CONFIG["MIN_SPREAD_THRESHOLD"] and spread2 > total_costs:
                        # Buy on Bybit, sell on Binance
                        entry_price = p["byb_a"]
                        if self.open_position(sym, "sell", "bybit", "binance", entry_price):
                            print(f"🔥 ARBITRAGE OPEN {sym} | Spread2: {float(spread2)*100:.3f}%")

            # 3. Hourly profit summary
            if now - self.last_hour_reset >= 3600:
                print(f"\n⏰ HOURLY PROFIT: +${self.hourly_profit:.4f} | Total balance: ${self.balance:.2f}\n")
                self.hourly_profit = Decimal("0")
                self.last_hour_reset = now

            # 4. Heartbeat
            if now - last_heartbeat > 15:
                synced = sum(1 for s in CONFIG["SYMBOLS"] if all(self.prices[s][k] is not None for k in ["bin_a","bin_b","byb_a","byb_b"]))
                print(f"📡 Status: {synced}/{len(CONFIG['SYMBOLS'])} pairs active | Open positions: {len(self.positions)} | Balance: ${self.balance:.2f}")
                last_heartbeat = now

            await asyncio.sleep(0.1)

    # ------------------- Main -------------------
    async def run(self):
        # Start WebSocket tasks
        tasks = [self.binance_pair_stream(s) for s in CONFIG["SYMBOLS"]]
        tasks.append(self.stream_bybit())
        tasks.append(self.engine())
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    bot = ArbitrageBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.running = False
        print("\nShutdown complete")
