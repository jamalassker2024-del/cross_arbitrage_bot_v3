#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MICRO WALLET ORDER FLOW BOT – $20 BALANCE, $0.10 MIN TRADE
- Uses Binance Convert (min $0.10 per trade)
- Extremely small position sizing
- High trade frequency – accumulates tiny profits
- Loss cooldown prevents account drain
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time
from collections import deque

getcontext().prec = 12

# ========== MICRO WALLET CONFIGURATION ==========
CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT", "PEPEUSDT", "SUIUSDT"],
    "INITIAL_BALANCE": Decimal("20.00"),           # $20 total
    "ORDER_SIZE_USDT": Decimal("0.50"),            # $0.50 per trade (5x Binance Convert min)
    "MIN_ORDER_USDT": Decimal("0.10"),             # Binance Convert minimum
    "OFI_LEVELS": 8,                               # Slightly fewer levels for speed
    "OFI_THRESHOLD": Decimal("0.60"),              # Extreme signals only
    "TAKE_PROFIT_BPS": Decimal("8"),               # 0.08% profit target
    "STOP_LOSS_BPS": Decimal("5"),                 # 0.05% stop loss
    "MAX_HOLD_SECONDS": 10,                        # Faster exits for micro trades
    "WIN_COOLDOWN_SEC": 1,                         # 1 second after win
    "LOSS_COOLDOWN_SEC": 30,                       # 30 seconds after loss
    "SCAN_INTERVAL_MS": 50,                        # 50ms scan for speed
    "REFRESH_BOOK_SEC": 30,                        # Refresh order book every 30s
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
}

MAKER_FEE = Decimal("0")   # Limit orders = 0% fee

class MicroOrderBook:
    def __init__(self, symbol):
        self.symbol = symbol
        self.bids = {}
        self.asks = {}
        self.last_update = 0.0
        self.last_refresh = 0.0

    def apply_depth(self, data):
        for side, key in [('bids', 'b'), ('asks', 'a')]:
            book_side = getattr(self, side)
            for price_str, qty_str in data.get(key, []):
                price, qty = Decimal(price_str), Decimal(qty_str)
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

    def get_ofi(self, depth=8):
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
                self.last_refresh = time.time()
                return True
        except Exception as e:
            print(f"❌ Refresh error {self.symbol}: {e}")
            return False

class MicroWalletBot:
    def __init__(self):
        self.order_books = {s: MicroOrderBook(s) for s in CONFIG["SYMBOLS"]}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.hourly_profit = Decimal('0')
        self.last_hour_reset = time.time()
        self.last_trade_time = {}
        self.last_trade_result = {}
        self.running = True

    async def load_snapshots(self, session):
        for sym in CONFIG["SYMBOLS"]:
            await self.order_books[sym].refresh_snapshot(session)
            print(f"✅ {sym} snapshot loaded")

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

    def open_micro_position(self, symbol, side):
        """Open tiny position using limit order at best bid/ask"""
        book = self.order_books[symbol]
        if side == 'buy':
            price = book.best_bid()
        else:
            price = book.best_ask()
        
        if price <= 0:
            return False
        
        # Calculate quantity based on order size
        order_size = CONFIG["ORDER_SIZE_USDT"]
        # Ensure we don't exceed remaining balance
        if order_size > self.balance * Decimal("0.95"):  # Leave 5% buffer
            order_size = self.balance * Decimal("0.9")
            if order_size < CONFIG["MIN_ORDER_USDT"]:
                return False
        
        qty = order_size / price
        cost = qty * price
        fee = cost * MAKER_FEE
        
        if cost + fee > self.balance:
            return False
        
        self.balance -= (cost + fee)
        self.positions[symbol] = {
            'side': side,
            'entry_price': price,
            'quantity': qty,
            'entry_time': time.time(),
            'order_size': order_size,
        }
        
        # Calculate expected profit for display
        expected_profit = order_size * (CONFIG["TAKE_PROFIT_BPS"] / Decimal("10000"))
        print(f"💰 OPEN {symbol} {side.upper()} @ {price:.4f} | Size: ${order_size:.2f} | Expected: +${expected_profit:.4f} | Balance: ${self.balance:.2f}")
        return True

    def close_micro_position(self, symbol, reason):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        
        book = self.order_books[symbol]
        if pos['side'] == 'buy':
            exit_price = book.best_ask()
        else:
            exit_price = book.best_bid()
        
        if exit_price <= 0:
            exit_price = book.mid_price()
        
        gross = pos['quantity'] * exit_price
        fee = gross * MAKER_FEE
        cost_basis = pos['quantity'] * pos['entry_price']
        profit = (gross - cost_basis) - fee
        
        self.balance += gross - fee
        self.total_trades += 1
        
        if profit > 0:
            self.winning_trades += 1
            self.last_trade_result[symbol] = 'win'
        else:
            self.last_trade_result[symbol] = 'loss'
        
        self.hourly_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"🎯 CLOSE {symbol} {reason} | Profit: ${profit:.4f} ({profit/pos['order_size']*100:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    async def run(self):
        async with aiohttp.ClientSession() as session:
            await self.load_snapshots(session)
        
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_depth(sym))
        
        print("\n🚀 MICRO WALLET ORDER FLOW BOT – $20 BALANCE")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']} | Min trade: ${CONFIG['MIN_ORDER_USDT']}")
        print(f"   Profit target: {float(CONFIG['TAKE_PROFIT_BPS'])/100:.2f}% | Stop loss: {float(CONFIG['STOP_LOSS_BPS'])/100:.2f}%\n")
        
        last_hb = 0
        last_ofi_debug = 0
        last_refresh = time.time()
        
        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()
                
                # Refresh order books periodically
                if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                    for sym in CONFIG["SYMBOLS"]:
                        await self.order_books[sym].refresh_snapshot(session)
                    last_refresh = now
                
                # Debug OFI every 5 seconds (show only strong signals)
                if now - last_ofi_debug > 5:
                    strong_signals = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_ofi(CONFIG["OFI_LEVELS"])
                        if abs(ofi) > 0.3:
                            strong_signals.append(f"{sym}:{ofi:.3f}")
                    if strong_signals:
                        print(f"🔍 Strong OFI: {' | '.join(strong_signals)}")
                    last_ofi_debug = now
                
                # 1. Close positions
                for sym in list(self.positions.keys()):
                    pos = self.positions[sym]
                    book = self.order_books[sym]
                    mid = book.mid_price()
                    if mid == 0:
                        continue
                    
                    if pos['side'] == 'buy':
                        profit_pct = (mid / pos['entry_price'] - 1) * 100
                    else:
                        profit_pct = (pos['entry_price'] / mid - 1) * 100
                    
                    if profit_pct >= float(CONFIG['TAKE_PROFIT_BPS']) / 100:
                        self.close_micro_position(sym, "TP")
                    elif profit_pct <= -float(CONFIG['STOP_LOSS_BPS']) / 100:
                        self.close_micro_position(sym, "SL")
                    elif now - pos['entry_time'] > CONFIG['MAX_HOLD_SECONDS']:
                        self.close_micro_position(sym, "TIMEOUT")
                
                # 2. Open new positions (only if balance > minimum order)
                if self.balance >= CONFIG["MIN_ORDER_USDT"] * 2:
                    for sym in CONFIG["SYMBOLS"]:
                        if sym in self.positions:
                            continue
                        
                        cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                        if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                            continue
                        
                        book = self.order_books[sym]
                        ofi = book.get_ofi(CONFIG["OFI_LEVELS"])
                        
                        if ofi > CONFIG["OFI_THRESHOLD"]:
                            print(f"⚡ {sym} OFI: {ofi:.3f} → BUY")
                            self.open_micro_position(sym, 'buy')
                        elif ofi < -CONFIG["OFI_THRESHOLD"]:
                            print(f"⚡ {sym} OFI: {ofi:.3f} → SELL")
                            self.open_micro_position(sym, 'sell')
                else:
                    if int(now) % 60 == 0:  # Print once per minute
                        print(f"⚠️ Low balance: ${self.balance:.2f} (minimum order: ${CONFIG['MIN_ORDER_USDT']})")
                
                # 3. Hourly profit summary
                if now - self.last_hour_reset >= 3600:
                    print(f"\n⏰ HOURLY PROFIT: +${self.hourly_profit:.4f} | Balance: ${self.balance:.2f} | Total trades: {self.total_trades}\n")
                    self.hourly_profit = Decimal('0')
                    self.last_hour_reset = now
                
                # 4. Heartbeat
                if now - last_hb > 30:
                    print(f"📡 Balance: ${self.balance:.2f} | Open: {len(self.positions)} | WinRate: {(self.winning_trades/self.total_trades*100) if self.total_trades else 0:.1f}%")
                    last_hb = now
                
                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    bot = MicroWalletBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        bot.running = False
        print("\nShutdown complete")
