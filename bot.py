#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FINAL PROFITABLE LIMIT SCALPER – 0% MAKER FEES, FAST EXITS
- REST snapshots + WebSocket depth diffs (reliable OFI)
- Limit entry & limit TP (0% fees)
- Market SL/timeout (0.1% fee only on losers)
- TP: 0.02% net profit | SL: 0.04% | Max hold: 6s
- High win rate, many trades per hour
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["DOGSUSDT", "NEIROUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "OFI_LEVELS": 5,
    "OFI_THRESHOLD": Decimal("0.55"),        # strong signals
    "TAKE_PROFIT_BPS": Decimal("2"),         # 0.02% pure profit (0% fee)
    "STOP_LOSS_BPS": Decimal("4"),           # 0.04% – wider than TP to avoid noise
    "MAX_HOLD_SECONDS": 6,                   # fast exit
    "ENTRY_TIMEOUT_SEC": 2,                  # cancel unfilled limit entry
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 15,
    "SCAN_INTERVAL_MS": 20,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

MAKER_FEE = Decimal("0")       # limit orders
TAKER_FEE = Decimal("0.001")   # market orders (SL/timeout)

class ProfitableLimitScalper:
    def __init__(self):
        self.order_books = {}
        self.positions = {}          # filled positions
        self.pending_entries = {}    # limit orders waiting to fill
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.last_trade_result = {}
        self.running = True

    class OrderBook:
        def __init__(self, symbol):
            self.symbol = symbol
            self.bids = {}
            self.asks = {}
            self.last_update = 0.0

        def apply_depth(self, data):
            for side, key in [('bids', 'b'), ('asks', 'a')]:
                book_side = getattr(self, side)
                for p_str, q_str in data.get(key, []):
                    price, qty = Decimal(p_str), Decimal(q_str)
                    if qty == 0:
                        book_side.pop(price, None)
                    else:
                        book_side[price] = qty
            self.last_update = time.time()

        def best_bid(self):
            return max(self.bids.keys()) if self.bids else Decimal('0')

        def best_ask(self):
            return min(self.asks.keys()) if self.asks else Decimal('0')

        def mid_price(self):
            bb, ba = self.best_bid(), self.best_ask()
            return (bb + ba) / 2 if bb and ba else Decimal('0')

        def tick_size(self):
            if self.bids:
                prices = sorted(self.bids.keys())
                if len(prices) > 1:
                    return abs(prices[1] - prices[0])
            return Decimal('0.00000001')

        def get_ofi(self, depth=5):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_vol = sum(q for _, q in sorted_bids)
            ask_vol = sum(q for _, q in sorted_asks)
            if bid_vol + ask_vol == 0:
                return Decimal('0')
            return (bid_vol - ask_vol) / (bid_vol + ask_vol)

        async def refresh_snapshot(self, session):
            url = f"https://api.binance.com/api/v3/depth?symbol={self.symbol}&limit=20"
            try:
                async with session.get(url) as resp:
                    data = await resp.json()
                    self.bids = {Decimal(p): Decimal(q) for p, q in data['bids']}
                    self.asks = {Decimal(p): Decimal(q) for p, q in data['asks']}
                    self.last_update = time.time()
                    return True
            except Exception:
                return False

    async def load_snapshots(self, session):
        for sym in CONFIG["SYMBOLS"]:
            self.order_books[sym] = self.OrderBook(sym)
            await self.order_books[sym].refresh_snapshot(session)
            print(f"✅ {sym} ready")

    async def subscribe_depth(self, symbol):
        stream = f"{symbol.lower()}@depth20@100ms"
        url = f"{CONFIG['BINANCE_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'b' in data or 'a' in data:
                            self.order_books[symbol].apply_depth(data)
            except Exception:
                await asyncio.sleep(3)

    def place_entry_limit(self, symbol, side):
        book = self.order_books[symbol]
        if side == 'buy':
            base = book.best_bid()
            if base <= 0:
                return
            price = base + book.tick_size()
            if price >= book.best_ask():
                price = book.best_ask() - book.tick_size()
        else:
            base = book.best_ask()
            if base <= 0:
                return
            price = base - book.tick_size()
            if price <= book.best_bid():
                price = book.best_bid() + book.tick_size()
        if price <= 0:
            return

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return

        qty = order_size / price
        self.pending_entries[symbol] = {
            'side': side,
            'price': price,
            'qty': qty,
            'order_size': order_size,
            'timestamp': time.time()
        }
        print(f"📝 LIMIT {side.upper()} Posted: {symbol} @ {price:.8f}")

    def check_fills_and_positions(self):
        now = time.time()

        # 1. Check pending entries
        for sym in list(self.pending_entries.keys()):
            order = self.pending_entries[sym]
            book = self.order_books[sym]
            filled = (order['side'] == 'buy' and book.best_ask() <= order['price']) or \
                     (order['side'] == 'sell' and book.best_bid() >= order['price'])
            if filled:
                self.balance -= order['order_size']
                tp = order['price'] * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 - CONFIG["TAKE_PROFIT_BPS"]/10000)
                sl = order['price'] * (1 - CONFIG["STOP_LOSS_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 + CONFIG["STOP_LOSS_BPS"]/10000)
                self.positions[sym] = {
                    'side': order['side'],
                    'entry': order['price'],
                    'qty': order['qty'],
                    'order_size': order['order_size'],
                    'tp': tp,
                    'sl': sl,
                    'time': now
                }
                del self.pending_entries[sym]
                print(f"✅ FILLED {sym} @ {order['price']:.8f} | TP limit @ {tp:.8f}")
            elif now - order['timestamp'] > CONFIG["ENTRY_TIMEOUT_SEC"]:
                del self.pending_entries[sym]
                print(f"⌛ ENTRY TIMEOUT: {sym} cancelled")

        # 2. Check active positions
        for sym in list(self.positions.keys()):
            pos = self.positions[sym]
            book = self.order_books[sym]
            # TP check (limit exit)
            hit_tp = (pos['side'] == 'buy' and book.best_bid() >= pos['tp']) or \
                     (pos['side'] == 'sell' and book.best_ask() <= pos['tp'])
            # SL check (market exit)
            hit_sl = (pos['side'] == 'buy' and book.best_bid() <= pos['sl']) or \
                     (pos['side'] == 'sell' and book.best_ask() >= pos['sl'])
            if hit_tp:
                self.close_trade(sym, pos['tp'], "WIN (LIMIT)")
            elif hit_sl:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_trade(sym, exit_price, "LOSS (SL MARKET)")
            elif now - pos['time'] > CONFIG["MAX_HOLD_SECONDS"]:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_trade(sym, exit_price, "TIMEOUT (MARKET)")

    def close_trade(self, sym, price, reason):
        pos = self.positions.pop(sym)
        is_market = "MARKET" in reason
        fee_rate = TAKER_FEE if is_market else MAKER_FEE
        gross = pos['qty'] * price
        fee_amt = gross * fee_rate
        cost = pos['qty'] * pos['entry']
        if pos['side'] == 'buy':
            profit = (gross - cost) - fee_amt
        else:
            profit = (cost - gross) - fee_amt
        self.balance += gross - fee_amt
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
            self.last_trade_result[sym] = 'win'
        else:
            self.last_trade_result[sym] = 'loss'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | PnL: ${profit:.4f} ({profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        async with aiohttp.ClientSession() as session:
            await self.load_snapshots(session)

        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_depth(sym))

        print("\n🚀 PROFITABLE LIMIT SCALPER ACTIVE")
        print(f"   TP: 0.02% pure profit | SL: 0.04% | Max hold: {CONFIG['MAX_HOLD_SECONDS']}s")
        print(f"   Entry timeout: {CONFIG['ENTRY_TIMEOUT_SEC']}s | OFI threshold: {CONFIG['OFI_THRESHOLD']}\n")

        last_ofi_print = 0
        last_refresh = time.time()
        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()

                # Refresh snapshots periodically to keep books accurate
                if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                    for sym in CONFIG["SYMBOLS"]:
                        await self.order_books[sym].refresh_snapshot(session)
                    last_refresh = now

                # Print OFI every 3 seconds
                if now - last_ofi_print > 3:
                    ofi_str = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        ofi_str.append(f"{sym}:{ofi:.3f}")
                    print(f"🔍 OFI: {' | '.join(ofi_str)}")
                    last_ofi_print = now

                self.check_fills_and_positions()

                # Open new positions – one per symbol concurrently
                for sym in CONFIG["SYMBOLS"]:
                    if sym in self.positions or sym in self.pending_entries:
                        continue
                    cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                        continue
                    ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                    if ofi > CONFIG["OFI_THRESHOLD"]:
                        print(f"⚡ {sym} OFI: {ofi:.3f} → LIMIT BUY")
                        self.place_entry_limit(sym, 'buy')
                    elif ofi < -CONFIG["OFI_THRESHOLD"]:
                        print(f"⚡ {sym} OFI: {ofi:.3f} → LIMIT SELL")
                        self.place_entry_limit(sym, 'sell')

                # Daily profit reset
                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.4f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(ProfitableLimitScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
