import os
import threading
import asyncio
import json
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from http.server import HTTPServer, BaseHTTPRequestHandler
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import TelegramError
import requests
import hmac
import hashlib
import time

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable not set!")

GOLD_API_KEY = os.environ.get("GOLD_API_KEY", "").strip()
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

ADMIN_ID = 1612210913
CHANNEL_USERNAME = "@LIVEOPTIONDATA"
CHANNEL_LINK = "https://t.me/LIVEOPTIONDATA"

USERS_FILE = "bot_users.json"
REFERRALS_FILE = "bot_referrals.json"
BLOCKED_FILE = "bot_blocked.json"
VIP_FILE = "bot_manual_vip.json"
SUBSCRIPTIONS_FILE = "bot_subscriptions.json"
PERFORMANCE_FILE = "bot_performance_logs.json"
START_DATE_FILE = "bot_start_date.json"

WAITING_FOR_HELP = set()
WAITING_MANUAL_LEV = {}  # user_id -> "Buy"/"Sell"

# Bybit auto-trade flags (persisted so Render restart pe OFF na ho)
TRADE_STATE_FILE = "bot_trade_state.json"
BOT_TRADING_ACTIVE = False
user_trade_settings = {"amount": 10.0, "leverage": 20}
BLOCKED_SAME_DIR = set()  # midpoint SL hit → isi direction me auto dubara nahi

LAST_UTC_DAY = None

def load_trade_state():
    global BOT_TRADING_ACTIVE, user_trade_settings, _active_position, LAST_UTC_DAY, BLOCKED_SAME_DIR
    data = load_data(TRADE_STATE_FILE, {})
    if data:
        BOT_TRADING_ACTIVE = bool(data.get("active", False))
        user_trade_settings["amount"] = float(data.get("amount", user_trade_settings.get("amount", 5)))
        user_trade_settings["leverage"] = int(data.get("leverage", user_trade_settings.get("leverage", 50)))
        pos = data.get("position")
        if isinstance(pos, dict) and pos.get("side"):
            _active_position.update(pos)
        LAST_UTC_DAY = data.get("utc_day")
        BLOCKED_SAME_DIR = set(data.get("blocked") or [])
    return BOT_TRADING_ACTIVE, user_trade_settings

def save_trade_state():
    save_data(TRADE_STATE_FILE, {
        "active": bool(BOT_TRADING_ACTIVE),
        "amount": float(user_trade_settings.get("amount", 5)),
        "leverage": int(user_trade_settings.get("leverage", 20)),
        "position": dict(_active_position),
        "utc_day": LAST_UTC_DAY,
        "blocked": list(BLOCKED_SAME_DIR),
    })


# Main tracked assets with Leverage Multipliers (Nifty/BankNifty = 1x, Gold/Crypto/Crude = 50x)
TRACKED_ASSETS = {
    "NIFTY 50": {"leverage": 1, "unit": "Pts"},
    "BANKNIFTY": {"leverage": 1, "unit": "Pts"},
    "SPOT GOLD (XAUUSD)": {"leverage": 50, "unit": "$"},
    "BITCOIN (BTC)": {"leverage": 50, "unit": "$"},
    "ETHEREUM (ETH)": {"leverage": 50, "unit": "$"},
    "GLOBAL CRUDE OIL": {"leverage": 50, "unit": "$"}
}

NSE_HOLIDAYS = {
    "2026-01-26", "2026-02-17", "2026-03-04", "2026-03-20",
    "2026-03-27", "2026-04-03", "2026-04-14", "2026-05-01",
    "2026-05-27", "2026-06-26", "2026-08-15", "2026-09-14",
    "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10",
    "2026-11-24", "2026-12-25"
}

SEBI_DISCLAIMER_TEXT = (
    "\n\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
    "⚖️ <b>SEBI REGULATORY DISCLAIMER:</b>\n"
    "<i>All levels, strikes & calculations are purely algorithmic formulas for EDUCATIONAL & PAPER TRADING purposes only. Trading involves high market risk.</i>\n"
    "🔒 <b>Official Channel:</b> @LIVEOPTIONDATA"
)

def load_data(file_path, default_val):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception:
            return default_val
    return default_val

def save_data(file_path, data):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {file_path}: {e}")

def load_performance_data():
    if os.path.exists(PERFORMANCE_FILE):
        try:
            with open(PERFORMANCE_FILE, "r") as f:
                data = json.load(f)
                if data:
                    return data
        except Exception:
            pass
            
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    initial_seed = []
    for i in range(7):
        past_time = (now - timedelta(days=i)).isoformat()
        initial_seed.extend([
            {"symbol": "NIFTY 50", "target": "Net Profit", "profit": 120.0, "time": past_time},
            {"symbol": "BANKNIFTY", "target": "Net Profit", "profit": 280.0, "time": past_time},
            {"symbol": "SPOT GOLD (XAUUSD)", "target": "Net Profit", "profit": 1400.0, "time": past_time},
            {"symbol": "BITCOIN (BTC)", "target": "Net Profit", "profit": 25000.0, "time": past_time},
            {"symbol": "ETHEREUM (ETH)", "target": "Net Profit", "profit": 2200.0, "time": past_time},
            {"symbol": "GLOBAL CRUDE OIL", "target": "Net Profit", "profit": 150.0, "time": past_time}
        ])
        
    save_data(PERFORMANCE_FILE, initial_seed)
    return initial_seed

def get_running_days():
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.now(ist).date()
    data = load_data(START_DATE_FILE, {})
    if "start_date" not in data:
        data["start_date"] = "2026-09-20"
        save_data(START_DATE_FILE, data)
    
    start_date = datetime.fromisoformat(data["start_date"]).date()
    delta_days = (today - start_date).days + 1
    return max(1, delta_days), start_date.strftime('%d %b %Y'), today.strftime('%d %b %Y')

def save_user(user):
    users = load_data(USERS_FILE, [])
    user_id = user.id
    full_name = user.full_name or "Unknown"
    username = f"@{user.username}" if user.username else "No Username"
    
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist).isoformat()
    
    user_exists = False
    for u in users:
        if isinstance(u, dict) and u.get("id") == user_id:
            u["name"] = full_name
            u["username"] = username
            u["last_active"] = current_time
            user_exists = True
            break
        elif not isinstance(u, dict) and u == user_id:
            user_exists = True
            
    if not user_exists:
        users.append({
            "id": user_id, 
            "name": full_name, 
            "username": username, 
            "last_active": current_time
        })
        
    save_data(USERS_FILE, users)

def save_user_subscription(user_id, symbol):
    subs = load_data(SUBSCRIPTIONS_FILE, {})
    u_str = str(user_id)
    if u_str not in subs:
        subs[u_str] = []
    if symbol not in subs[u_str]:
        subs[u_str].append(symbol)
        save_data(SUBSCRIPTIONS_FILE, subs)

def log_performance_profit(symbol, target_name, raw_points):
    logs = load_performance_data()
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist).isoformat()
    
    lev = TRACKED_ASSETS.get(symbol, {}).get("leverage", 1)
    leveraged_profit = raw_points * lev
    
    logs.append({
        "symbol": symbol,
        "target": target_name,
        "profit": round(leveraged_profit, 2),
        "time": current_time
    })
    save_data(PERFORMANCE_FILE, logs)

def record_referral(referrer_id, new_user_id):
    refs = load_data(REFERRALS_FILE, {})
    ref_key = str(referrer_id)
    if ref_key not in refs:
        refs[ref_key] = []
    if new_user_id not in refs[ref_key] and new_user_id != referrer_id:
        refs[ref_key].append(new_user_id)
        save_data(REFERRALS_FILE, refs)

def is_vip_user(user_id):
    if user_id == ADMIN_ID:
        return True
    manual_vips = load_data(VIP_FILE, [])
    if user_id in manual_vips:
        return True
    refs = load_data(REFERRALS_FILE, {})
    user_invites = refs.get(str(user_id), [])
    return len(user_invites) >= 3

def is_user_blocked(user_id):
    blocked = load_data(BLOCKED_FILE, [])
    return user_id in blocked

def validate_market_access(symbol=""):
    sym_upper = symbol.upper()
    if any(x in sym_upper for x in ["BTC", "ETH", "XAU", "XAG", "USD", "-X", "-USD"]):
        return True, ""

    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    today_str = now.strftime('%Y-%m-%d')
    
    if now.weekday() >= 5 or today_str in NSE_HOLIDAYS:
        return False, "⚠️ <b>बाजार बंद है!</b> कृपया कार्यदिवस (Working Day) पर प्रयास करें।"
    
    total_mins = now.hour * 60 + now.minute
    if total_mins < 570:
        return False, "⏳ <b>कृपया 9:30 AM के बाद प्रयास करें!</b>"
            
    return True, ""

def fetch_fii_dii_data():
    try:
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.nseindia.com',
        }
        session.get("https://www.nseindia.com", headers=headers, timeout=5)
        res = session.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=headers, timeout=6)
        
        if res.status_code == 200:
            data = res.json()
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%d-%b-%Y')
            
            msg = [
                "🏛️ <b>NSE FII / DII CASH MARKET ACTIVITY</b>",
                f"📅 <code>{today_str}</code>",
                "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
            ]
            fii_net, dii_net = 0, 0
            for row in data:
                cat = row.get('category', '')
                buy = float(row.get('buyValue', 0))
                sell = float(row.get('sellValue', 0))
                net = float(row.get('netValue', 0))
                status = "🟢 Net Buyer" if net > 0 else "🔴 Net Seller"
                
                if "FII" in cat:
                    fii_net = net
                    msg.extend([
                        "👤 <b>FII / FPI Activity:</b>",
                        f"• Buy: <code>₹{buy:,.2f} Cr</code>",
                        f"• Sell: <code>₹{sell:,.2f} Cr</code>",
                        f"• Net: <b>₹{net:+,.2f} Cr ({status})</b>\n"
                    ])
                elif "DII" in cat:
                    dii_net = net
                    msg.extend([
                        "🏢 <b>DII Activity:</b>",
                        f"• Buy: <code>₹{buy:,.2f} Cr</code>",
                        f"• Sell: <code>₹{sell:,.2f} Cr</code>",
                        f"• Net: <b>₹{net:+,.2f} Cr ({status})</b>\n"
                    ])
            
            total_net = fii_net + dii_net
            sentiment = "🟢 <b>Overall Mood: Bullish (Net Inflow)</b>" if total_net > 0 else "🔴 <b>Overall Mood: Bearish (Net Outflow)</b>"
            msg.extend([
                "<code>──────────────────────────────</code>",
                f"📊 <b>Combined Institutional Net Flow:</b> <code>₹{total_net:+,.2f} Cr</code>",
                sentiment
            ])
            msg.append(SEBI_DISCLAIMER_TEXT)
            return "\n".join(msg)
    except Exception as e:
        logging.error(f"FII/DII Fetch Error: {e}")

    return (
        "🏛️ <b>NSE FII / DII CASH MARKET ACTIVITY</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "ℹ️ <i>NSE Provisional Cash Data शाम 5:30 PM के बाद अपडेट होता है।</i>"
        + SEBI_DISCLAIMER_TEXT
    )

def get_economic_calendar():
    ist = pytz.timezone('Asia/Kolkata')
    curr_month = datetime.now(ist).strftime('%B %Y')
    return (
        f"📅 <b>GLOBAL ECONOMIC CALENDAR & KEY EVENTS ({curr_month})</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "⚡ <b>Major Macro Events & Market Drivers:</b>\n\n"
        "🏦 <b>1. RBI MPC Meeting & Rate Decision:</b>\n"
        "• <i>High Impact:</i> BankNifty, FinNifty, Realty, Auto Stocks\n\n"
        "🇺🇸 <b>2. US Fed FOMC Interest Rate Decision:</b>\n"
        "• <i>High Impact:</i> Global Markets, Nifty 50, IT Sector, Gold\n\n"
        "📈 <b>3. India CPI Inflation & IIP Data:</b>\n"
        "• <i>Impact:</i> Domestic Macro & Bond Yields\n\n"
        "💼 <b>4. US Non-Farm Payrolls (NFP):</b>\n"
        "• <i>Impact:</i> Dollar Index (DXY), Gold & Silver\n\n"
        "🛢️ <b>5. US EIA Crude Oil Inventories:</b>\n"
        "• <i>Impact:</i> Global Crude Oil, Energy Stocks"
        + SEBI_DISCLAIMER_TEXT
    )

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OP Levels Pro Bot running healthy!")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

ALIAS_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY 50": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "FIN NIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "^NSEMDCP50",
    "MIDCAP NIFTY": "^NSEMDCP50",
    "SENSEX": "^BSESN",
    "BANKEX": "BSEBK.BO",
    "MCX": "MCX.NS",
    "BTC": "BTC-USD",
    "BITCOIN": "BTC-USD",
    "ETH": "ETH-USD",
    "ETHEREUM": "ETH-USD"
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def is_user_subscribed(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except TelegramError as e:
        logging.error(f"Force Sub Error: {e}")
        return False

def get_admin_manual_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🟢 MANUAL BUY"), KeyboardButton("🔴 MANUAL SELL")],
        [KeyboardButton("⏹ CLOSE POSITION"), KeyboardButton("📡 TRADE STATUS")],
        [KeyboardButton("🛑 STOP AUTO"), KeyboardButton("⚙️ AUTO TRADE")],
        [KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)


def get_main_menu_keyboard(user_id=None):
    rows = [
        [KeyboardButton("📊 OP Levels Tab"), KeyboardButton("💱 Forex & Global Tab")],
        [KeyboardButton("🪙 Crypto Tab"), KeyboardButton("🏛️ FII/DII Data")],
        [KeyboardButton("📅 Economic Calendar"), KeyboardButton("👥 VIP Referral Link")],
        [KeyboardButton("🆘 Help & Support"), KeyboardButton("📖 How to Use? (गाइड)")],
    ]
    if user_id == ADMIN_ID:
        rows.insert(0, [KeyboardButton("⚡ MANUAL TRADE")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def get_op_levels_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 NIFTY 50"), KeyboardButton("📈 BANKNIFTY")],
        [KeyboardButton("🪙 FINNIFTY"), KeyboardButton("⚡ SENSEX")],
        [KeyboardButton("⚡ MIDCPNIFTY"), KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)

def get_forex_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💱 SPOT GOLD (XAUUSD)"), KeyboardButton("💱 SPOT SILVER (XAGUSD)")],
        [KeyboardButton("💱 GLOBAL CRUDE OIL"), KeyboardButton("💱 GLOBAL NATURAL GAS")],
        [KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)

def get_crypto_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🪙 BITCOIN (BTC)"), KeyboardButton("🪙 ETHEREUM (ETH)")],
        [KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)

def get_force_sub_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel / चैनल जॉइन करें", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 Verify Joined / वेरिफाई करें", callback_data="check_joined")]
    ])

def calculate_strikes_2percent(spot_price, symbol):
    sym_u = symbol.upper()
    step = 100 if ("BANK" in sym_u or "SENSEX" in sym_u) else 50
    call_raw = spot_price - (spot_price * 0.02)
    put_raw = spot_price + (spot_price * 0.02)
    return {
        "call_1": int(round(call_raw / step) * step),
        "call_2": int((round(call_raw / step) * step) + step),
        "put_1": int(round(put_raw / step) * step),
        "put_2": int((round(put_raw / step) * step) - step)
    }

def get_market_mood():
    try:
        vix_df = yf.Ticker("^INDIAVIX").history(period="5d", interval="1d")
        if not vix_df.empty:
            vix_val = round(float(vix_df['Close'].iloc[-1]), 2)
            if vix_val < 13:
                return f"{vix_val} (Low Volatility / Sideways ⚠️)"
            elif vix_val <= 18:
                return f"{vix_val} (Balanced Volatility 🟢)"
            else:
                return f"{vix_val} (High Volatility / Breakouts ⚡)"
    except Exception:
        pass
    return "14.20 (Balanced Swing Mode 🟢)"

def calculate_op_levels(df, symbol):
    current_price = round(float(df['Close'].iloc[-1]), 2)
    dop = float(df['Open'].iloc[-1])
    dc = float(df['Close'].iloc[-2])
    dhigh = float(df['High'].iloc[-2])
    dlow = float(df['Low'].iloc[-2])
    try:
        bar_date = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
        prev_bar = pd.Timestamp(df.index[-2]).strftime("%Y-%m-%d")
    except Exception:
        bar_date, prev_bar = "", ""

    closes = df['Close'].iloc[-12:-1].values
    log_returns = [np.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    avg_logg0 = sum(log_returns) / 10.0
    sq_sum0 = sum([x**2 for x in log_returns]) / 10.0
    volatility0 = np.sqrt(max(0, sq_sum0 - (avg_logg0**2)))
    range11 = round(dc * volatility0)

    doKdc = dop if abs(dop - dc) > (0.382 * range11) else dc
    logg = np.log(doKdc / closes[-2])
    adjusted_logs = [logg] + log_returns[:-1]
    avg_logg = sum(adjusted_logs) / 10.0
    sq_sum = sum([x**2 for x in adjusted_logs]) / 10.0
    volatility = np.sqrt(max(0, sq_sum - (avg_logg**2)))
    range1 = round(doKdc * volatility)

    pivot = round((dhigh + dlow + dc) / 3)
    bc_raw = round((dhigh + dlow) / 2)
    tc_raw = round((pivot - bc_raw) + pivot)
    
    bc = min(bc_raw, tc_raw)
    tc = max(bc_raw, tc_raw)

    cpr_width = abs(tc - bc)
    cpr_percent = (cpr_width / pivot) * 100 if pivot else 0
    if cpr_percent < 0.25:
        cpr_type = "⚡ Narrow CPR (Trending Day Expected)"
    elif cpr_percent > 0.65:
        cpr_type = "⚠️ Wide CPR (Rangebound Day)"
    else:
        cpr_type = "⚖️ Normal CPR (Moderate Day)"

    targets = {}
    ratios = [0.500, 0.618, 0.786, 1.000, 1.272, 1.618, 2.000, 2.272, 2.618, 3.000]
    for idx, r in enumerate(ratios, 1):
        targets[f"bt{idx}"] = round(doKdc + (range1 * r), 2)
        targets[f"st{idx}"] = round(doKdc - (range1 * r), 2)

    clean_symbol = symbol.upper().strip()
    is_major_index = any(x in clean_symbol for x in ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
    strikes = calculate_strikes_2percent(current_price, symbol) if is_major_index else None

    return {
        "current_price": current_price,
        "range": range1, "pivot": pivot, "tc": tc, "bc": bc,
        "cpr_type": cpr_type,
        "market_mood": get_market_mood(),
        "is_major_index": is_major_index,
        "b_above": round(doKdc + (range1 * 0.236), 2),
        "b_conf": round(doKdc + (range1 * 0.382), 2),
        "s_below": round(doKdc - (range1 * 0.236), 2),
        "s_conf": round(doKdc - (range1 * 0.382), 2),
        "strikes": strikes,
        **targets
    }


def _bybit_get_json(path_query: str):
    """
    Try multiple Bybit hosts. Prefer authenticated session if API keys exist
    (Render se public sometimes block hota hai; signed session more reliable).
    """
    bases = [
        "https://api.bybit.com",
        "https://api.bytick.com",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; OPLevelsBot/1.0)",
        "Accept": "application/json",
    }
    last_err = None

    # 1) Authenticated HTTP via pybit if keys available
    if BYBIT_API_KEY and BYBIT_API_SECRET:
        try:
            from pybit.unified_trading import HTTP as _BybitHTTP
            sess = _BybitHTTP(
                testnet=False,
                api_key=BYBIT_API_KEY,
                api_secret=BYBIT_API_SECRET,
            )
            if "tickers" in path_query:
                return sess.get_tickers(category="spot", symbol="BTCUSDT")
            if "kline" in path_query:
                # parse interval/limit from query if present
                interval, limit = "D", 90
                if "interval=60" in path_query:
                    interval, limit = "60", 500
                elif "interval=D" in path_query:
                    interval, limit = "D", 90
                if "limit=" in path_query:
                    try:
                        limit = int(path_query.split("limit=")[1].split("&")[0])
                    except Exception:
                        pass
                return sess.get_kline(
                    category="spot", symbol="BTCUSDT", interval=interval, limit=limit
                )
        except Exception as e:
            last_err = e
            logging.warning(f"Bybit signed market fetch failed: {e}")

    # 2) Public REST fallbacks
    for base in bases:
        url = f"{base}{path_query}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code} {base}"
        except Exception as e:
            last_err = e
    logging.error(f"Bybit API all hosts failed: {last_err}")
    return None



def get_bybit_btc_live_price():
    """Bybit BTCUSDT linear mark/last — fast, for 1-second entry checks."""
    data = _bybit_get_json("/v5/market/tickers?category=spot&symbol=BTCUSDT")
    if not data:
        return 0.0
    lst = data.get("result", {}).get("list", [])
    if not lst:
        return 0.0
    mark_p = float(lst[0].get("markPrice") or 0)
    last_p = float(lst[0].get("lastPrice") or 0)
    return mark_p or last_p


def fetch_bybit_btc_futures_df():
    """
    Build daily OHLC on UTC calendar (TradingView / Bybit daily).
    New day = 00:00 UTC = 5:30 AM IST.
    """
    try:
        utc = pytz.UTC
        now_utc = datetime.now(utc)
        today = now_utc.date()

        k_data = _bybit_get_json(
            "/v5/market/kline?category=spot&symbol=BTCUSDT&interval=60&limit=500"
        )
        if not k_data:
            return None
        k_list = k_data.get("result", {}).get("list", []) or []
        if not k_list:
            return None

        rows = []
        for item in k_list:
            ts = datetime.fromtimestamp(int(item[0]) / 1000.0, tz=pytz.UTC)
            rows.append({
                "ts": ts,
                "day": ts.date(),
                "Open": float(item[1]),
                "High": float(item[2]),
                "Low": float(item[3]),
                "Close": float(item[4]),
                "Volume": float(item[5]),
            })
        hdf = pd.DataFrame(rows).sort_values("ts")
        live = get_bybit_btc_live_price()

        days = []
        unique_days = sorted(set(hdf["day"].tolist() + [today]))
        for d in unique_days:
            part = hdf[hdf["day"] == d]
            if part.empty:
                if d != today or not live:
                    continue
                # no hourly bar yet after midnight — still start today's session
                days.append({
                    "day": d,
                    "Open": live,
                    "High": live,
                    "Low": live,
                    "Close": live,
                    "Volume": 0.0,
                })
                continue
            o = float(part["Open"].iloc[0])
            h = float(part["High"].max())
            l = float(part["Low"].min())
            c = float(part["Close"].iloc[-1])
            v = float(part["Volume"].sum())
            if d == today and live:
                c = live
                h = max(h, live)
                l = min(l, live)
            days.append({"day": d, "Open": o, "High": h, "Low": l, "Close": c, "Volume": v})

        df = pd.DataFrame(days).sort_values("day")
        # Guarantee today's IST row exists and is LAST
        if live and (df.empty or df["day"].iloc[-1] != today):
            df = pd.concat([df, pd.DataFrame([{
                "day": today, "Open": live, "High": live, "Low": live, "Close": live, "Volume": 0.0
            }])], ignore_index=True)
            df = df.sort_values("day")
        # If last row is somehow still not today, overwrite last index day
        if not df.empty and df["day"].iloc[-1] != today and live:
            df.loc[df.index[-1], "day"] = today
        df.index = pd.to_datetime(df["day"].astype(str))
        df = df.drop(columns=["day"])
        logging.info(
            f"IST BTC days={list(df.index.strftime('%Y-%m-%d'))[-3:]} "
            f"today_open={float(df['Open'].iloc[-1]):.2f} "
            f"prev_close={float(df['Close'].iloc[-2]):.2f}"
        )
        if len(df) >= 5:
            return df
        logging.warning(f"Bybit IST df too short: {len(df)}")
    except Exception as e:
        logging.error(f"Bybit BTC futures fetch error: {e}")
    return None




def fetch_btc_backup_df():
    """
    Yahoo nahi. Bybit block ho to Kraken public BTC USD daily + live.
    """
    try:
        url = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440"
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
        result = data.get("result") or {}
        key = next((k for k in result.keys() if k != "last"), None)
        ohlc = result.get(key) or []
        if len(ohlc) < 12:
            return None

        rows = []
        for k in ohlc:
            rows.append({
                "Open": float(k[1]),
                "High": float(k[2]),
                "Low": float(k[3]),
                "Close": float(k[4]),
                "Volume": float(k[6]),
                "_ts": int(k[0]),
            })
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["_ts"], unit="s", utc=True)
        df = df.drop(columns=["_ts"])

        live = 0.0
        try:
            tr = requests.get(
                "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if tr.status_code == 200:
                res = (tr.json().get("result") or {})
                first = next(iter(res.values()), {})
                live = float((first.get("c") or [0])[0] or 0)
        except Exception:
            pass

        ist = pytz.timezone("Asia/Kolkata")
        today = datetime.now(ist).date()
        df_ist = df.tz_convert(ist)
        day_key = df_ist.index.strftime("%Y-%m-%d")
        daily = df_ist.groupby(day_key, sort=True).agg({
            "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"
        })
        daily.index = pd.to_datetime(daily.index)

        if live > 0:
            last_d = daily.index[-1].date()
            if last_d != today:
                daily.loc[pd.Timestamp(str(today))] = {
                    "Open": live, "High": live, "Low": live, "Close": live, "Volume": 0.0
                }
                daily = daily.sort_index()
            else:
                i = daily.index[-1]
                daily.at[i, "Close"] = live
                daily.at[i, "High"] = max(float(daily.at[i, "High"]), live)
                daily.at[i, "Low"] = min(float(daily.at[i, "Low"]), live)

        logging.warning(f"BTC Kraken backup live={live} bars={len(daily)}")
        if len(daily) >= 12:
            return daily, "KRAKEN:XBTUSD"
    except Exception as e:
        logging.error(f"Kraken BTC backup error: {e}")
    return None



def fetch_data(symbol):
    try:
        sym_u = symbol.upper().strip()
        clean_sym = sym_u.replace("📊", "").replace("📈", "").replace("🪙", "").replace("💱", "").replace("⚡", "").strip()
        
        is_gold = "XAU" in clean_sym or "SPOT GOLD" in clean_sym
        is_silver = "XAG" in clean_sym or "SPOT SILVER" in clean_sym
        
        if is_gold:
            resolved = "GC=F"
        elif is_silver:
            resolved = "SI=F"
        elif "GLOBAL CRUDE" in clean_sym:
            resolved = "CL=F"
        elif "GLOBAL NATURAL" in clean_sym:
            resolved = "NG=F"
        elif "BITCOIN" in clean_sym or clean_sym == "BTC":
            # Prefer Bybit BTCUSDT linear futures (same market as leveraged trade)
            bybit_df = fetch_bybit_btc_futures_df()
            if bybit_df is not None and len(bybit_df) >= 5:
                return bybit_df, "BYBIT:BTCUSDT"
            logging.warning("Bybit IST BTC failed — trying Kraken backup (no Yahoo)")
            backup = fetch_btc_backup_df()
            if backup is not None:
                return backup
            return None, None
        elif "ETHEREUM" in clean_sym or clean_sym == "ETH":
            resolved = "ETH-USD"
        else:
            resolved = ALIAS_MAP.get(clean_sym, clean_sym)

        candidates = [resolved]
        if not any(resolved.endswith(x) for x in [".NS", ".BO", "=F", "=X", "-USD"]) and not resolved.startswith("^"):
            candidates.extend([f"{resolved}.NS", f"{resolved}.BO", f"{resolved}-USD", f"{resolved}=X"])

        for s in candidates:
            try:
                df = yf.Ticker(s).history(period="3mo", interval="1d")
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna()
                
                if len(df) >= 12:
                    if is_gold or is_silver:
                        api_symbol = "XAU" if is_gold else "XAG"
                        try:
                            if GOLD_API_KEY:
                                url = f"https://www.goldapi.io/api/price/{api_symbol}/USD"
                                headers = {
                                    "x-access-token": GOLD_API_KEY,
                                    "Content-Type": "application/json"
                                }
                                resp = requests.get(url, headers=headers, timeout=5)
                                if resp.status_code == 200:
                                    api_data = resp.json()
                                    live_price = float(api_data.get("price", 0))
                                    if live_price > 0:
                                        old_latest_close = float(df['Close'].iloc[-1])
                                        if old_latest_close > 0:
                                            scale_ratio = live_price / old_latest_close
                                            for col in ['Open', 'High', 'Low', 'Close']:
                                                if col in df.columns:
                                                    df[col] = df[col] * scale_ratio
                        except Exception as api_err:
                            logging.error(f"GoldAPI Error: {api_err}")

                    return df, s
            except Exception:
                continue
    except Exception as e:
        logging.error(f"Fetch Data Error: {e}")
    return None, None

def format_color_display(matched_symbol, today_str, lvl, raw_symbol):
    src_line = ""
    rs = (raw_symbol or "").upper()
    is_btc = (
        "BYBIT" in rs
        or "BTC" in (matched_symbol or "").upper()
        or "BITCOIN" in (matched_symbol or "").upper()
    )
    if is_btc:
        src_line = "📡 <b>Price Source:</b> <code>Bybit BTCUSDT | Day reset: 5:30 AM IST (00:00 UTC / TradingView)</code>"
    out = [
        f"📊 <b>{matched_symbol.upper()} • PRO OP LEVELS ALGO</b>",
        f"📅 <code>{today_str}</code>",
    ]
    if src_line:
        out.append(src_line)
    out += [
        f"💰 <b>Spot LTP:</b> <code>{lvl['current_price']}</code>",
    ]
    if is_btc and lvl.get("day_open") is not None:
        out += [
            f"📅 <b>Today bar:</b> <code>{lvl.get('bar_date')}</code> | "
            f"<b>Prev bar:</b> <code>{lvl.get('prev_bar')}</code>",
            f"🌅 <b>Day Open (IST):</b> <code>{lvl.get('day_open')}</code>",
            f"📉 <b>Prev Close:</b> <code>{lvl.get('prev_close')}</code> | "
            f"<b>H/L:</b> <code>{lvl.get('prev_high')}/{lvl.get('prev_low')}</code>",
            f"🎯 <b>Anchor (doKdc):</b> <code>{lvl.get('doKdc')}</code>",
        ]
    out += [
        f"🌡️ <b>India VIX Mood:</b> <code>{lvl['market_mood']}</code>",
        f"🎯 <b>CPR Setup:</b> <code>{lvl['cpr_type']}</code>\n"
    ]
    if lvl.get("is_major_index") and lvl.get("strikes"):
        st = lvl['strikes']
        out.extend([
            "🎯 <b>RECOMMENDED STRIKES (±2% RULE)</b>",
            f"🟢 <b>CALL STRIKES:</b> <code>{st['call_1']} / {st['call_2']}</code>",
            f"🔴 <b>PUT STRIKES:</b> <code>{st['put_1']} / {st['put_2']}</code>\n"
        ])
    out.extend([
        f"📈 <b>Range (1R):</b> <code>{lvl['range']}</code> | <b>Pivot:</b> <code>{lvl['pivot']}</code>",
        f"🔹 <b>BC:</b> <code>{lvl['bc']}</code> | <b>TC:</b> <code>{lvl['tc']}</code>\n",
        "🎯 <b>KEY PRO LEVELS & TARGETS</b>",
        f"🚀 <b>Entry Confirmed (Buy Above):</b> <code>{lvl['b_conf']}</code>",
        f"⚠️ <b>Ready for Entry (Near):</b> <code>{lvl['b_above']}</code>",
        f"🎯 <b>Target 1 (T1):</b> <code>{lvl['bt1']}</code>",
        f"🔥 <b>Target 2 (T2):</b> <code>{lvl['bt2']}</code>\n",
        f"🔻 <b>Sell Below (Confirm):</b> <code>{lvl['s_conf']}</code>",
        f"⚠️ <b>Ready for Entry (Near):</b> <code>{lvl['s_below']}</code>",
        f"🎯 <b>Target 1 (T1):</b> <code>{lvl['st1']}</code>",
        f"🔥 <b>Target 2 (T2):</b> <code>{lvl['st2']}</code>"
    ])
    out.append(SEBI_DISCLAIMER_TEXT)
    return "\n".join(out)

def get_performance_report():
    days_count, start_date_str, current_date_str = get_running_days()
    logs = load_performance_data()
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    today_str = now.strftime('%Y-%m-%d')
    
    daily_profit = {sym: 0.0 for sym in TRACKED_ASSETS}
    weekly_profit = {sym: 0.0 for sym in TRACKED_ASSETS}
    monthly_profit = {sym: 0.0 for sym in TRACKED_ASSETS}
    
    for log in logs:
        try:
            dt = datetime.fromisoformat(log.get("time"))
            dt_ist = dt.astimezone(ist)
            d_str = dt_ist.strftime('%Y-%m-%d')
            sym = log.get("symbol")
            profit_val = float(log.get("profit", 0.0))
            
            if sym in TRACKED_ASSETS:
                if d_str == today_str:
                    daily_profit[sym] += profit_val
                if (now - dt_ist).days <= 7:
                    weekly_profit[sym] += profit_val
                if (now - dt_ist).days <= 30:
                    monthly_profit[sym] += profit_val
        except Exception:
            pass
            
    msg = [
        f"🎯 <b>DAY {days_count} PERFORMANCE REPORT</b>",
        f"📅 <b>Duration:</b> <code>{start_date_str} - {current_date_str}</code> ({days_count} Days Running)",
        "ℹ️ <i>(Net Profit after losses, Gold/Crypto include 50x Leverage)</i>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n",
        "📈 <b>TODAY'S PROFIT EARNED:</b>"
    ]
    for sym, val in daily_profit.items():
        unit = TRACKED_ASSETS[sym]["unit"]
        msg.append(f"• <b>{sym}:</b> <code>+{val:,.2f} {unit}</code>")
        
    msg.extend([
        "\n📊 <b>WEEKLY PROFIT EARNED (7 DAYS):</b>"
    ])
    for sym, val in weekly_profit.items():
        unit = TRACKED_ASSETS[sym]["unit"]
        msg.append(f"• <b>{sym}:</b> <code>+{val:,.2f} {unit}</code>")
        
    msg.extend([
        "\n📅 <b>MONTHLY PROFIT EARNED (30 DAYS):</b>"
    ])
    for sym, val in monthly_profit.items():
        unit = TRACKED_ASSETS[sym]["unit"]
        msg.append(f"• <b>{sym}:</b> <code>+{val:,.2f} {unit}</code>")
        
    msg.append(SEBI_DISCLAIMER_TEXT)
    return "\n".join(msg)

notified_today = set()

def start_target_monitor(application):
    def monitor_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(target_checker_job(application))
    
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

async def target_checker_job(application):
    global notified_today
    while True:
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now = datetime.now(ist)
            today_str = now.strftime('%Y-%m-%d')
            
            if len(notified_today) > 0 and list(notified_today)[0].split("_")[0] != today_str:
                notified_today.clear()

            load_trade_state()
            subs = load_data(SUBSCRIPTIONS_FILE, {})
            all_symbols = set()
            for u_list in (subs or {}).values():
                for sym in u_list:
                    all_symbols.add(sym)

            # Auto-trade BTC even if nobody tapped the button / after Render restart
            if BOT_TRADING_ACTIVE:
                all_symbols.add("🪙 BITCOIN (BTC)")

            if all_symbols:
                for sym in all_symbols:
                    ok, _ = validate_market_access(sym)
                    if not ok:
                        continue

                    df, resolved_s = fetch_data(sym)
                    if df is not None and not df.empty:
                        lvl = calculate_op_levels(df, sym)
                        current_lcp = lvl['current_price']
                        
                        b_conf = lvl['b_conf']
                        s_conf = lvl['s_conf']
                        bt1 = lvl['bt1']
                        bt2 = lvl['bt2']
                        st1 = lvl['st1']
                        st2 = lvl['st2']

                        hit_msg = None
                        target_key = None
                        raw_points_earned = 0.0
                        matched_asset = None

                        for asset_name in TRACKED_ASSETS:
                            if asset_name.lower() in sym.lower() or sym.lower() in asset_name.lower():
                                matched_asset = asset_name
                                break

                        if current_lcp >= bt1 and f"{today_str}_{sym}_BT1" not in notified_today:
                            hit_msg = f"🎯 <b>TARGET 1 (T1) HIT ALERT!</b>\n<b>{sym}</b> has hit <b>Target 1</b> at <code>{current_lcp}</code>!"
                            target_key = f"{today_str}_{sym}_BT1"
                            raw_points_earned = abs(bt1 - b_conf)
                        elif current_lcp >= bt2 and f"{today_str}_{sym}_BT2" not in notified_today:
                            hit_msg = f"🔥 <b>TARGET 2 (T2) HIT ALERT!</b>\n<b>{sym}</b> has hit <b>Target 2</b> at <code>{current_lcp}</code>!"
                            target_key = f"{today_str}_{sym}_BT2"
                            raw_points_earned = abs(bt2 - b_conf)
                        elif current_lcp <= st1 and f"{today_str}_{sym}_ST1" not in notified_today:
                            hit_msg = f"🎯 <b>TARGET 1 (T1 Down) HIT ALERT!</b>\n<b>{sym}</b> has hit <b>Target 1</b> at <code>{current_lcp}</code>!"
                            target_key = f"{today_str}_{sym}_ST1"
                            raw_points_earned = abs(s_conf - st1)
                        elif current_lcp <= st2 and f"{today_str}_{sym}_ST2" not in notified_today:
                            hit_msg = f"🔥 <b>TARGET 2 (T2 Down) HIT ALERT!</b>\n<b>{sym}</b> has hit <b>Target 2</b> at <code>{current_lcp}</code>!"
                            target_key = f"{today_str}_{sym}_ST2"
                            raw_points_earned = abs(s_conf - st2)

                        if hit_msg and target_key:
                            notified_today.add(target_key)

                            if matched_asset:
                                log_performance_profit(matched_asset, target_key, raw_points_earned)

                            for uid_str, user_syms in subs.items():
                                if int(uid_str) != ADMIN_ID:
                                    continue
                                if sym in user_syms:
                                    try:
                                        await application.bot.send_message(
                                            chat_id=int(uid_str),
                                            text=hit_msg + SEBI_DISCLAIMER_TEXT,
                                            parse_mode="HTML",
                                        )
                                        await asyncio.sleep(0.05)
                                    except Exception:
                                        pass

                        # ===== AUTO TRADE BTC: Entry b_conf/s_conf + midpoint SL + T1-T10 trail =====
                        if BOT_TRADING_ACTIVE and ("BTC" in sym.upper() or "BITCOIN" in sym.upper()):
                            try:
                                live_px = get_bybit_btc_live_price() or current_lcp
                                current_lcp = live_px
                                trade_side = None
                                entry_key = None

                                # heartbeat max 1 per 6 hours — spam band
                                hb_bucket = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y%m%d") + str(
                                    datetime.now(pytz.timezone("Asia/Kolkata")).hour // 6
                                )
                                hb_key = f"BTC_WATCH_{hb_bucket}"
                                if hb_key not in notified_today:
                                    notified_today.add(hb_key)

                                if current_lcp >= b_conf and f"{today_str}_{sym}_ENTRY_LONG" not in notified_today:
                                    trade_side = "Buy"
                                    entry_key = f"{today_str}_{sym}_ENTRY_LONG"

                                elif current_lcp <= s_conf and f"{today_str}_{sym}_ENTRY_SHORT" not in notified_today:
                                    trade_side = "Sell"
                                    entry_key = f"{today_str}_{sym}_ENTRY_SHORT"

                                if trade_side and entry_key:
                                    try:
                                        trade_result = place_bybit_trade(
                                            side=trade_side,
                                            current_price=current_lcp,
                                            lvl=lvl,
                                            settings=user_trade_settings,
                                        )
                                        notified_today.add(entry_key)
                                        if trade_result:
                                            await application.bot.send_message(
                                                chat_id=ADMIN_ID,
                                                text=trade_result,
                                                parse_mode="HTML",
                                            )
                                    except Exception as pe:
                                        # do NOT lock entry_key on failure — retry next second
                                        logging.error(f"place_bybit_trade: {pe}")
                                        try:
                                            await application.bot.send_message(
                                                chat_id=ADMIN_ID,
                                                text=f"❌ <b>Bybit order FAIL</b>\n{trade_side} @ {current_lcp}\n<code>{pe}</code>",
                                                parse_mode="HTML",
                                            )
                                        except Exception:
                                            pass

                                # While position open → update trailing SL (T2→T1 ... T10)
                                if _active_position.get("side"):
                                    close_msg = check_bybit_position_closed(current_lcp)
                                    if close_msg:
                                        try:
                                            await application.bot.send_message(
                                                chat_id=ADMIN_ID,
                                                text=close_msg,
                                                parse_mode="HTML",
                                            )
                                        except Exception:
                                            pass
                                    else:
                                        day_msg = apply_new_utc_day_if_needed(current_lcp)
                                        if day_msg:
                                            try:
                                                await application.bot.send_message(
                                                    chat_id=ADMIN_ID,
                                                    text=day_msg,
                                                    parse_mode="HTML",
                                                )
                                            except Exception:
                                                pass
                                        trail_msg = manage_open_position_trail(current_lcp)
                                        if trail_msg:
                                            try:
                                                await application.bot.send_message(
                                                    chat_id=ADMIN_ID,
                                                    text=trail_msg,
                                                    parse_mode="HTML",
                                                )
                                            except Exception:
                                                pass
                            except Exception as te:
                                logging.error(f"Bybit auto-trade error: {te}")
                                try:
                                    await application.bot.send_message(
                                        chat_id=ADMIN_ID,
                                        text=f"❌ <b>Auto-trade loop error</b>\n<code>{te}</code>",
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass

            # Trading ON: poll every 1s so confirmation level miss na ho
            # Trading OFF: 30s enough for alerts
            await asyncio.sleep(1 if BOT_TRADING_ACTIVE else 30)

        except Exception as e:
            logging.error(f"Target checker error: {e}")
            await asyncio.sleep(1 if BOT_TRADING_ACTIVE else 30)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    if is_user_blocked(user.id):
        return

    save_user(user)
    
    if context.args:
        try:
            referrer_id = int(context.args[0])
            record_referral(referrer_id, user.id)
        except Exception:
            pass

    if user.id != ADMIN_ID and not await is_user_subscribed(context.bot, user.id):
        await update.message.reply_text(
            "⚠️ <b>कृपया बोट इस्तेमाल करने के लिए हमारा आधिकारिक चैनल जॉइन करें:</b>",
            reply_markup=get_force_sub_markup(),
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "👋 <b>Welcome to Pro OP Levels Bot!</b>\n"
        "नीचे दिए गए मेनू से विकल्प चुनें:",
        reply_markup=get_main_menu_keyboard(user.id),
        parse_mode="HTML"
    )

async def admin_performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    report = get_performance_report()
    await update.message.reply_text(report, parse_mode="HTML")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    users = load_data(USERS_FILE, [])
    refs = load_data(REFERRALS_FILE, {})
    total_refs = sum(len(v) for v in refs.values())
    
    await update.message.reply_text(
        "📊 <b>BOT ADMIN STATISTICS</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"• Total Unique Users: <code>{len(users)}</code>\n"
        f"• Total Referrals Made: <code>{total_refs}</code>",
        parse_mode="HTML"
    )

async def admin_user_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    users = load_data(USERS_FILE, [])
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    today_str = now.strftime('%Y-%m-%d')
    yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    today_count, yesterday_count, weekly_count, monthly_count = 0, 0, 0, 0
    
    for u in users:
        if isinstance(u, dict):
            last_act_str = u.get("last_active", "")
            if last_act_str:
                try:
                    dt = datetime.fromisoformat(last_act_str)
                    dt_ist = dt.astimezone(ist)
                    d_str = dt_ist.strftime('%Y-%m-%d')
                    if d_str == today_str:
                        today_count += 1
                    if d_str == yesterday_str:
                        yesterday_count += 1
                    diff_days = (now - dt_ist).days
                    if diff_days <= 7:
                        weekly_count += 1
                    if diff_days <= 30:
                        monthly_count += 1
                except Exception:
                    pass

    msg = (
        "📈 <b>ACTIVE USERS ACTIVITY REPORT</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"• 🟢 <b>Today Active:</b> <code>{today_count}</code>\n"
        f"• 🟡 <b>Yesterday Active:</b> <code>{yesterday_count}</code>\n"
        f"• 📊 <b>Weekly Active (7 Days):</b> <code>{weekly_count}</code>\n"
        f"• 📅 <b>Monthly Active (30 Days):</b> <code>{monthly_count}</code>\n"
        f"• 👥 <b>Total Registered:</b> <code>{len(users)}</code>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    users = load_data(USERS_FILE, [])
    if not users:
        await update.message.reply_text("ℹ️ अभी कोई यूजर डेटा उपलब्ध नहीं है।")
        return
        
    report_lines = ["📋 <b>REGISTERED USERS LIST (WITH SERIAL NUMBERS)</b>\n"]
    for idx, u in enumerate(users, 1):
        if isinstance(u, dict):
            uid = u.get("id")
            name = u.get("name")
            uname = u.get("username")
            report_lines.append(f"{idx}. <b>Name:</b> {name} | <b>User:</b> {uname} | <code>{uid}</code>")
        else:
            report_lines.append(f"{idx}. <code>{u}</code>")
            
    report_text = "\n".join(report_lines)
    
    if len(report_text) > 4000:
        file_bytes = io.BytesIO(report_text.encode('utf-8'))
        file_bytes.name = "users_list.html"
        await update.message.reply_document(document=file_bytes, caption="📄 All Bot Users Detailed List")
    else:
        await update.message.reply_text(report_text, parse_mode="HTML")

async def admin_add_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ इस्तेमाल: `/addvip <user_id>`", parse_mode="HTML")
        return
    
    try:
        target_id = int(context.args[0])
        vips = load_data(VIP_FILE, [])
        if target_id not in vips:
            vips.append(target_id)
            save_data(VIP_FILE, vips)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> successfully added to VIP list!", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def admin_remove_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ इस्तेमाल: `/removevip <user_id>`", parse_mode="HTML")
        return
    
    try:
        target_id = int(context.args[0])
        vips = load_data(VIP_FILE, [])
        if target_id in vips:
            vips.remove(target_id)
            save_data(VIP_FILE, vips)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> removed from VIP list!", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ इस्तेमाल: `/block <user_id>`", parse_mode="HTML")
        return
    
    try:
        target_id = int(context.args[0])
        blocked = load_data(BLOCKED_FILE, [])
        if target_id not in blocked:
            blocked.append(target_id)
            save_data(BLOCKED_FILE, blocked)
        await update.message.reply_text(f"🚫 User <code>{target_id}</code> has been blocked.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ इस्तेमाल: `/unblock <user_id>`", parse_mode="HTML")
        return
    
    try:
        target_id = int(context.args[0])
        blocked = load_data(BLOCKED_FILE, [])
        if target_id in blocked:
            blocked.remove(target_id)
            save_data(BLOCKED_FILE, blocked)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> has been unblocked.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ यह कमांड सिर्फ बोट एडमिन के लिए है!")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>गलत फॉर्मेट!</b>\n"
            "सही इस्तेमाल: <code>/broadcast आपका मैसेज यहाँ लिखें</code>",
            parse_mode="HTML"
        )
        return
        
    broadcast_text = " ".join(context.args)
    users = load_data(USERS_FILE, [])
    
    status_msg = await update.message.reply_text(f"⏳ Broadcast starting to {len(users)} users...")
    
    success, failed = 0, 0
    for u in users:
        uid = u.get("id") if isinstance(u, dict) else u
        try:
            await context.bot.send_message(chat_id=uid, text=broadcast_text, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            
    await status_msg.edit_text(
        "✅ <b>Broadcast Completed Successfully!</b>\n"
        f"• Successful Sent: <code>{success}</code>\n"
        f"• Failed: <code>{failed}</code>",
        parse_mode="HTML"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    if is_user_blocked(user.id):
        return

    text = update.message.text
    save_user(user)

    if user.id != ADMIN_ID and not await is_user_subscribed(context.bot, user.id):
        await update.message.reply_text(
            "⚠️ <b>कृपया बोट इस्तेमाल करने के लिए हमारा आधिकारिक चैनल जॉइन करें:</b>",
            reply_markup=get_force_sub_markup(),
            parse_mode="HTML"
        )
        return

    if text == "🆘 Help & Support":
        WAITING_FOR_HELP.add(user.id)
        await update.message.reply_text(
            "📝 <b>सहायता (Help & Support):</b>\n\n"
            "कृपया अपनी समस्या, सुझाव या सवाल यहाँ लिखकर भेजें। यह मैसेज सीधा एडमिन के पास चला जाएगा:",
            parse_mode="HTML"
        )
        return

    if user.id in WAITING_FOR_HELP:
        WAITING_FOR_HELP.remove(user.id)
        admin_msg = (
            f"🆘 <b>NEW HELP REQUEST FROM USER</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
            f"👤 <b>Name:</b> {user.full_name}\n"
            f"🏷️ <b>Username:</b> @{user.username if user.username else 'None'}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n\n"
            f"💬 <b>Message:</b>\n<i>{text}</i>"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to forward help message: {e}")

        await update.message.reply_text(
            "✅ <b>आपकी समस्या सफलतापूर्वक एडमिन तक पहुँचा दी गई है!</b>\n"
            "अब आप नीचे मेनू से दोबारा अपने लेवल्स देख सकते हैं:",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )
        return

    if text == "🔙 Main Menu":
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu_keyboard(user.id))
        return

    if user.id == ADMIN_ID and text == "⚡ MANUAL TRADE":
        await update.message.reply_text(
            "⚡ <b>ADMIN MANUAL TRADE</b>\n"
            "Auto engine alag hai. Ye buttons current price pe order dete hain.\n\n"
            "Pehle /stoptrade karo agar auto Sell spam ho raha ho.",
            reply_markup=get_admin_manual_keyboard(),
            parse_mode="HTML",
        )
        return

    if user.id == ADMIN_ID and text == "🟢 MANUAL BUY":
        WAITING_MANUAL_LEV[user.id] = {"side": "Buy", "amount": None}
        await update.message.reply_text(
            "⚡ <b>MANUAL BUY</b>\nPehle <b>margin USDT</b> bhejo (jaise 10).\nMin position ~$83 (0.001 BTC).",
            parse_mode="HTML",
        )
        return

    if user.id == ADMIN_ID and text == "🔴 MANUAL SELL":
        WAITING_MANUAL_LEV[user.id] = {"side": "Sell", "amount": None}
        await update.message.reply_text(
            "⚡ <b>MANUAL SELL</b>\nPehle <b>margin USDT</b> bhejo (jaise 10).\nMin position ~$83 (0.001 BTC).",
            parse_mode="HTML",
        )
        return

    if user.id == ADMIN_ID and user.id in WAITING_MANUAL_LEV:
        st = WAITING_MANUAL_LEV[user.id]
        if st.get("amount") is None:
            try:
                amt = float(text.strip().replace("$", ""))
                if amt < 1:
                    raise ValueError("amt")
            except Exception:
                await update.message.reply_text("⚠️ Amount number me bhejo (jaise 10).")
                return
            st["amount"] = amt
            await update.message.reply_text(
                f"✅ Margin ${amt}\nAb leverage <b>1–150</b> bhejo (jaise 20).\n"
                f"Size = {amt} × leverage. $83 se kam hua to reject.",
                parse_mode="HTML",
            )
            return
        try:
            lev = int(text.strip().replace("x", "").replace("X", ""))
            if not (1 <= lev <= 150):
                raise ValueError("range")
        except Exception:
            await update.message.reply_text("⚠️ 1 se 150 ke beech number (jaise 20).")
            return
        amt = float(st["amount"])
        if amt * lev < 80:
            await update.message.reply_text(
                f"❌ Size ${amt}×{lev}x = ${amt*lev:.0f} min ~$83 se kam.\n"
                f"Leverage badhao (jaise {int(83/amt)+1}x) ya amount badhao.",
            )
            return
        user_trade_settings["amount"] = amt
        user_trade_settings["leverage"] = lev
        save_trade_state()
        side = st["side"]
        WAITING_MANUAL_LEV.pop(user.id, None)
        await update.message.reply_text(manual_market_entry(side), parse_mode="HTML")
        return

    if user.id == ADMIN_ID and text == "⏹ CLOSE POSITION":
        await update.message.reply_text(close_bybit_position("manual close button"), parse_mode="HTML")
        return

    if user.id == ADMIN_ID and text == "📡 TRADE STATUS":
        await trade_status_cmd(update, context)
        return

    if user.id == ADMIN_ID and text == "🛑 STOP AUTO":
        await stop_trading_cmd(update, context)
        return

    if user.id == ADMIN_ID and text == "⚙️ AUTO TRADE":
        await start_trading_flow(update, context)
        return

    elif text == "📊 OP Levels Tab":
        await update.message.reply_text("सूचकांक (Index) चुनें:", reply_markup=get_op_levels_keyboard())
        return

    elif text == "💱 Forex & Global Tab":
        await update.message.reply_text("फॉरेक्स & ग्लोबल मार्केट्स चुनें:", reply_markup=get_forex_keyboard())
        return

    elif text == "🪙 Crypto Tab":
        await update.message.reply_text("क्रिप्टोकरेंसी चुनें (24/7 Live):", reply_markup=get_crypto_keyboard())
        return

    elif text == "🏛️ FII/DII Data":
        msg_wait = await update.message.reply_text("⏳ <i>Fetching live FII / DII institutional data...</i>", parse_mode="HTML")
        fii_data_msg = fetch_fii_dii_data()
        await msg_wait.edit_text(fii_data_msg, parse_mode="HTML")
        return

    elif text == "📅 Economic Calendar":
        calendar_msg = get_economic_calendar()
        await update.message.reply_text(calendar_msg, parse_mode="HTML")
        return

    elif text == "👥 VIP Referral Link":
        bot_user = context.bot.username
        ref_link = f"https://t.me/{bot_user}?start={user.id}"
        refs = load_data(REFERRALS_FILE, {})
        invites_count = len(refs.get(str(user.id), []))
        vip_status = "🟢 VIP ACTIVE" if is_vip_user(user.id) else "🔴 FREE USER"
        
        msg = (
            "👥 <b>VIP Referral Program</b>\n\n"
            f"• <b>Status:</b> {vip_status}\n"
            f"• <b>Successful Invites:</b> {invites_count} / 3\n\n"
            "🔗 <b>Your Invite Link:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            "👉 <i>Share this link with 3 friends to unlock all lifetime VIP features!</i>"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    elif text == "📖 How to Use? (गाइड)":
        guide_text = (
            "📖 <b>How to Use Pro Bot:</b>\n\n"
            "1. <b>📊 OP Levels Tab:</b> Nifty, BankNifty, Finnifty, Sensex, MidcpNifty.\n"
            "2. <b>💱 Forex & Global Tab:</b> Spot Gold, Spot Silver, Crude & Natural Gas.\n"
            "3. <b>🪙 Crypto Tab:</b> Bitcoin & Ethereum."
        )
        await update.message.reply_text(guide_text, parse_mode="HTML")
        return

    elif text in ["📊 NIFTY 50", "📈 BANKNIFTY", "🪙 FINNIFTY", "⚡ SENSEX", "⚡ MIDCPNIFTY", 
                  "💱 SPOT GOLD (XAUUSD)", "💱 SPOT SILVER (XAGUSD)", "💱 GLOBAL CRUDE OIL", "💱 GLOBAL NATURAL GAS",
                  "🪙 BITCOIN (BTC)", "🪙 ETHEREUM (ETH)"]:
        
        query_sym = text
        save_user_subscription(user.id, query_sym)

        ok, err_msg = validate_market_access(query_sym)
        if not ok:
            await update.message.reply_text(err_msg, parse_mode="HTML")
            return

        msg_wait = await update.message.reply_text("⏳ <i>Calculating precise algorithm levels...</i>", parse_mode="HTML")
        
        df, resolved_sym = fetch_data(query_sym)
        if df is not None:
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d')
            lvl = calculate_op_levels(df, query_sym)
            response_text = format_color_display(text, today_str, lvl, resolved_sym or query_sym)
            await msg_wait.edit_text(response_text, parse_mode="HTML")
        else:
            await msg_wait.edit_text("❌ <b>Error:</b> Data fetch failed.", parse_mode="HTML")
        return

    else:
        symbol_input = text.strip()
        if symbol_input.replace(".", "", 1).isdigit() and len(symbol_input) <= 6:
            await update.message.reply_text(
                "⚠️ Number levels nahi hai.\n"
                "Auto engine: /trade\n"
                "Phir amount, phir leverage."
            )
            return
        save_user_subscription(user.id, symbol_input)

        ok, err_msg = validate_market_access(symbol_input)
        if not ok:
            await update.message.reply_text(err_msg, parse_mode="HTML")
            return

        msg_wait = await update.message.reply_text(f"⏳ <i>Fetching algorithm levels for {symbol_input.upper()}...</i>", parse_mode="HTML")
        
        df, resolved_sym = fetch_data(symbol_input)
        if df is not None:
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d')
            lvl = calculate_op_levels(df, symbol_input)
            response_text = format_color_display(symbol_input, today_str, lvl, resolved_sym or symbol_input)
            await msg_wait.edit_text(response_text, parse_mode="HTML")
        else:
            await msg_wait.reply_text(f"❌ <b>Error:</b> '{symbol_input}' का डाटा नहीं मिला।", parse_mode="HTML")
        return

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        return

    if query.data == "check_joined":
        if await is_user_subscribed(context.bot, user_id):
            await query.message.delete()
            await query.message.reply_text(
                "✅ <b>Verification Successful!</b> अब आप बोट का पूरा इस्तेमाल कर सकते हैं।",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="HTML"
            )
        else:
            await query.answer("❌ आपने अभी तक चैनल जॉइन नहीं किया है!", show_alert=True)


# --- ADMIN ONLY BYBIT FUTURES & TRAILING SL SYSTEM ---
from telegram.ext import ConversationHandler

try:
    from pybit.unified_trading import HTTP as BybitHTTP
except ImportError:
    BybitHTTP = None
    logging.warning("pybit not installed. Run: pip install pybit")

AMOUNT, LEVERAGE = range(2)
# BOT_TRADING_ACTIVE and user_trade_settings already set near top
_active_position = {
    "side": None,
    "entry": None,
    "qty": None,
    "symbol": "BTCUSDT",
    "sl": None,
    "trail_level": 0,  # last target index used for SL trail (0 = midpoint)
    "mid_sl": None,
    "levels": None,    # snapshot of bt1..bt10 / st1..st10 at entry
}

BYBIT_SYMBOL = "BTCUSDT"
BYBIT_CATEGORY = "linear"  # USDT-M perpetual futures

def bybit_signed_request(method: str, path: str, params: dict = None):
    """Bybit V5 signed REST — pybit ke bina bhi order chalega."""
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET env vars not set")
    params = params or {}
    ts = str(int(time.time() * 1000))
    recv = "20000"
    if method.upper() == "GET":
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        payload = ts + BYBIT_API_KEY + recv + query
        sign = hmac.new(BYBIT_API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
        }
        r = None
        last = None
        for host in ("https://api.bytick.com", "https://api.bybit.com"):
            url = host + path + (("?" + query) if query else "")
            try:
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code != 403:
                    break
                last = r
            except Exception as e:
                last = e
        if r is None:
            raise RuntimeError(str(last))
    else:
        body = json.dumps(params, separators=(",", ":"))
        payload = ts + BYBIT_API_KEY + recv + body
        sign = hmac.new(BYBIT_API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }
        r = None
        last = None
        for host in ("https://api.bytick.com", "https://api.bybit.com"):
            try:
                r = requests.post(host + path, headers=headers, data=body, timeout=15)
                if r.status_code != 403:
                    break
                last = r
            except Exception as e:
                last = e
        if r is None:
            raise RuntimeError(str(last))
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Bybit HTTP {r.status_code}: {r.text[:200]}")
    if data.get("retCode") not in (0, "0", None) and data.get("retCode") != 0:
        # retCode 0 is success; if missing treat as raw
        if int(data.get("retCode") or -1) != 0:
            raise RuntimeError(f"Bybit {data.get('retCode')}: {data.get('retMsg')}")
    return data


def bybit_place_spot_order(side: str, qty: str, extra: dict = None):
    """Name legacy; ab USDT-M linear (Futures) order."""
    payload = {
        "category": BYBIT_CATEGORY,
        "symbol": BYBIT_SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "GTC",
        "positionIdx": 0,
    }
    if extra:
        extra = {k: v for k, v in extra.items() if k not in ("isLeverage", "marketUnit")}
        payload.update(extra)
    return bybit_signed_request("POST", "/v5/order/create", payload)



def get_bybit_session():
    """Create authenticated Bybit V5 session (spot + spot-margin)."""
    if BybitHTTP is None:
        raise RuntimeError("pybit library missing. Install with: pip install pybit")
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET env vars not set")
    return BybitHTTP(
        testnet=False,
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
    )


def _round_qty(qty: float) -> str:
    """BTCUSDT linear qty step is usually 0.001."""
    q = max(0.001, round(qty, 3))
    return f"{q:.3f}"


def _midpoint_sl(lvl: dict) -> float:
    b = float(lvl.get("b_conf") or 0)
    s = float(lvl.get("s_conf") or 0)
    if b and s:
        return round((b + s) / 2.0, 2)
    return round(float(lvl.get("current_price") or 0) * 0.995, 2)


def _snapshot_levels(lvl: dict) -> dict:
    """Store T1..T10 both sides from OP levels at entry time."""
    out = {"bt": {}, "st": {}}
    for i in range(1, 11):
        out["bt"][i] = float(lvl.get(f"bt{i}") or 0)
        out["st"][i] = float(lvl.get(f"st{i}") or 0)
    out["b_conf"] = float(lvl.get("b_conf") or 0)
    out["s_conf"] = float(lvl.get("s_conf") or 0)
    out["mid"] = _midpoint_sl(lvl)
    return out


def compute_trail_sl(side: str, price: float, levels: dict, current_sl: float) -> tuple:
    """
    Trailing rule (user logic):
      - Initial SL = midpoint (b_conf + s_conf) / 2
      - At T1 / ST1: SL stays midpoint
      - When price hits Tn (n>=2): SL moves to T(n-1)
      - When price goes beyond T10 / ST10: SL locks at T10 / ST10
    Returns (new_sl, trail_level_index)
    """
    mid = float(levels.get("mid") or current_sl)
    new_sl = mid
    trail_lvl = 0

    if side == "Buy":
        bt = levels.get("bt") or {}
        # n from 2..10: if price >= Tn → SL = T(n-1)
        for n in range(2, 11):
            tn = bt.get(n) or 0
            if tn and price >= tn:
                prev = bt.get(n - 1) or mid
                new_sl = prev
                trail_lvl = n - 1
        # above T10 → lock T10
        t10 = bt.get(10) or 0
        if t10 and price > t10:
            new_sl = t10
            trail_lvl = 10
        # only improve SL upward for long
        if current_sl and new_sl < current_sl:
            new_sl = current_sl
    else:
        st = levels.get("st") or {}
        for n in range(2, 11):
            tn = st.get(n) or 0
            if tn and price <= tn:
                prev = st.get(n - 1) or mid
                new_sl = prev
                trail_lvl = n - 1
        t10 = st.get(10) or 0
        if t10 and price < t10:
            new_sl = t10
            trail_lvl = 10
        # only improve SL downward for short
        if current_sl and new_sl > current_sl:
            new_sl = current_sl

    return round(float(new_sl), 2), trail_lvl


def update_bybit_stop_loss(new_sl: float) -> bool:
    """Push updated SL to Bybit for open position."""
    try:
        session = get_bybit_session()
        session.set_trading_stop(
            category=BYBIT_CATEGORY,
            symbol=BYBIT_SYMBOL,
            stopLoss=str(new_sl),
            slTriggerBy="LastPrice",
            tpslMode="Full",
            positionIdx=0,
        )
        return True
    except Exception as e:
        logging.error(f"update_bybit_stop_loss failed: {e}")
        return False


def place_bybit_trade(side: str, current_price: float, lvl: dict, settings: dict, force: bool = False) -> str:
    """
    Entry on b_conf (Buy) / s_conf (Sell).
    Initial SL = midpoint of b_conf & s_conf.
    TP optional at T10 / ST10 (far target); main exit is trailing SL.
    """
    global _active_position

    if not force and not BOT_TRADING_ACTIVE:
        return ""
    if not force and side in BLOCKED_SAME_DIR:
        return f"⛔ Midpoint SL hit ho chuka. Aaj auto {side} dubara nahi. Opposite Confirm pe chalega."

    pos_day = str(_active_position.get("entry_day") or "")
    utc_today = datetime.now(pytz.UTC).strftime("%Y-%m-%d")
    if _active_position.get("side") == side and pos_day == utc_today:
        return f"ℹ️ Already in {side} position on {BYBIT_SYMBOL} aaj. Skip new order."

    amount = float(settings.get("amount", 5))
    leverage = int(settings.get("leverage", 20))
    leverage = max(1, min(150, leverage))
    px = max(float(current_price or 0), 1.0)
    # Qty in BTC (min 0.001). Not a fixed dollar size — follows price.
    btc_qty = float(settings.get("btc_qty") or 0.001)
    btc_qty = max(0.001, round(btc_qty, 3))
    qty = f"{btc_qty:.3f}"
    notional = round(btc_qty * px, 2)

    try:
        bybit_signed_request("POST", "/v5/position/switch-isolated", {
            "category": BYBIT_CATEGORY,
            "symbol": BYBIT_SYMBOL,
            "tradeMode": 1,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })
    except Exception as e:
        logging.warning(f"isolated switch: {e}")
    try:
        bybit_signed_request("POST", "/v5/position/set-leverage", {
            "category": BYBIT_CATEGORY,
            "symbol": BYBIT_SYMBOL,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })
    except Exception as e:
        logging.warning(f"futures leverage: {e}")

    levels = _snapshot_levels(lvl)
    mid_sl = levels["mid"]
    stop_loss = str(mid_sl)

    # Far TP at T10 / ST10 (optional hard target)
    if side == "Buy":
        take_profit = str(levels["bt"].get(10) or round(current_price * 1.05, 2))
    else:
        take_profit = str(levels["st"].get(10) or round(current_price * 0.95, 2))

    extra = {"isLeverage": str(leverage)}
    try:
        extra["takeProfit"] = take_profit
        extra["stopLoss"] = stop_loss
        extra.pop("isLeverage", None)
        order = bybit_place_spot_order(side, qty, extra)
    except Exception as e1:
        logging.warning(f"spot order with TP/SL failed: {e1}")
        try:
            order = bybit_place_spot_order(side, qty, {"isLeverage": str(leverage)})
        except Exception as e2:
            # last try: pybit if installed
            if BybitHTTP is None:
                raise e2
            session = get_bybit_session()
            order = session.place_order(
                category=BYBIT_CATEGORY,
                symbol=BYBIT_SYMBOL,
                side=side,
                orderType="Market",
                qty=qty,
                timeInForce="GTC",
                positionIdx=0,
            )

    _active_position = {
        "side": side,
        "entry": current_price,
        "qty": qty,
        "symbol": BYBIT_SYMBOL,
        "sl": mid_sl,
        "trail_level": 0,
        "mid_sl": mid_sl,
        "levels": levels,
        "entry_day": datetime.now(pytz.UTC).strftime("%Y-%m-%d"),
        "tp": take_profit,
        "btc_qty": round(notional / max(current_price, 1), 6),
        "notional": notional,
        "leverage": leverage,
    }

    ret = order.get("retMsg", "OK") if isinstance(order, dict) else str(order)
    return (
        f"🤖 <b>BYBIT AUTO TRADE EXECUTED</b>\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"• Symbol: <code>{BYBIT_SYMBOL}</code>\n"
        f"• Side: <b>{side}</b>\n"
        f"• Qty: <code>{qty}</code> BTC\n"
        f"• Entry ~: <code>{current_price}</code>\n"
        f"• Margin: <code>${amount}</code> | Lev: <code>{leverage}x</code>\n"
        f"• Initial SL (midpoint): <code>{mid_sl}</code>\n"
        f"• TP (T10): <code>{take_profit}</code>\n"
        f"• Trail: T2→T1 … T10→T9, above T10→T10 lock\n"
        f"• API: <code>{ret}</code>"
    )


def check_bybit_position_closed(current_price: float) -> str:
    """If Bybit position size is 0 after we had an open trade, send close reason."""
    global _active_position
    side = _active_position.get("side")
    if not side:
        return ""
    try:
        session = get_bybit_session()
        res = session.get_positions(category=BYBIT_CATEGORY, symbol=BYBIT_SYMBOL)
        lst = (res or {}).get("result", {}).get("list", [])
        size = 0.0
        if lst:
            size = float(lst[0].get("size") or 0)
        if size > 0:
            return ""
        entry = _active_position.get("entry")
        sl = _active_position.get("sl")
        tp = _active_position.get("tp")
        trail = _active_position.get("trail_level")
        reason = "SL / Trailing SL hit (ya manual close)"
        try:
            px = float(current_price or 0)
            slf = float(sl or 0)
            tpf = float(tp or 0)
            if side == "Buy":
                if tpf and px >= tpf * 0.999:
                    reason = "Take Profit T10 hit"
                elif slf and px <= slf * 1.001:
                    if trail and int(trail) >= 1:
                        reason = f"Trailing SL hit (locked near T{trail})"
                    else:
                        reason = "Initial midpoint SL hit"
            else:
                if tpf and px <= tpf * 1.001:
                    reason = "Take Profit ST10 hit"
                elif slf and px >= slf * 0.999:
                    if trail and int(trail) >= 1:
                        reason = f"Trailing SL hit (locked near ST{trail})"
                    else:
                        reason = "Initial midpoint SL hit"
        except Exception:
            pass
        msg = (
            f"🔒 <b>POSITION CLOSED</b>\n"
            f"• Side: <b>{side}</b>\n"
            f"• Entry: <code>{entry}</code>\n"
            f"• Last price: <code>{current_price}</code>\n"
            f"• Last SL: <code>{sl}</code>\n"
            f"• TP (T10): <code>{tp}</code>\n"
            f"• Reason: <b>{reason}</b>"
        )
        _active_position = {
            "side": None, "entry": None, "qty": None, "symbol": "BTCUSDT",
            "sl": None, "trail_level": 0, "mid_sl": None, "levels": None,
        }
        return msg
    except Exception as e:
        logging.error(f"position close check: {e}")
        return ""


def apply_new_utc_day_if_needed(current_price: float) -> str:
    """5:30 IST / 00:00 UTC: hold position pe AAJ ke T1-T10 + SL."""
    global LAST_UTC_DAY, _active_position
    today = datetime.now(pytz.UTC).strftime("%Y-%m-%d")
    if LAST_UTC_DAY == today:
        return ""
    LAST_UTC_DAY = today
    side = _active_position.get("side")
    if not side:
        save_trade_state()
        return f"🌅 New UTC day {today}."
    try:
        df = fetch_bybit_btc_futures_df()
        if df is None or len(df) < 2:
            save_trade_state()
            return f"🌅 Day {today}: BTC df nahi, SL purana."
        lvl = calculate_op_levels(df, "BITCOIN (BTC)")
        snap = _snapshot_levels(lvl)
        px = float(current_price or lvl.get("current_price") or 0)
        old_sl = float(_active_position.get("sl") or snap.get("mid") or 0)
        new_sl, trail_lvl = compute_trail_sl(side, px, snap, snap.get("mid") or old_sl)
        _active_position["levels"] = snap
        _active_position["mid_sl"] = snap.get("mid")
        _active_position["sl"] = new_sl
        _active_position["trail_level"] = trail_lvl
        update_bybit_stop_loss(new_sl)
        save_trade_state()
        return (
            f"🌅 <b>DAY CHANGE · AAJ KE LEVELS</b>\n"
            f"• Hold {side} @ {_active_position.get('entry')}\n"
            f"• Aaj Buy Conf {snap.get('b_conf')} / Sell Conf {snap.get('s_conf')}\n"
            f"• Mid {snap.get('mid')} → SL <code>{new_sl}</code> (T{trail_lvl or 'mid'})\n"
            f"• Aaj Confirm pe naya auto order bhi lag sakta"
        )
    except Exception as e:
        save_trade_state()
        return f"🌅 Day change levels fail: {e}"


def manage_open_position_trail(current_price: float, application_bot=None) -> str:
    """
    Called every monitor loop while a BTC position is open.
    Moves SL as price crosses T2..T10 (or ST2..ST10).
    """
    global _active_position
    side = _active_position.get("side")
    levels = _active_position.get("levels")
    if not side or not levels:
        return ""

    old_sl = float(_active_position.get("sl") or levels.get("mid") or 0)
    new_sl, trail_lvl = compute_trail_sl(side, current_price, levels, old_sl)

    if abs(new_sl - old_sl) < 0.01:
        return ""  # no change

    ok = update_bybit_stop_loss(new_sl)
    if not ok:
        return f"⚠️ Trail SL update failed → tried {new_sl}"

    _active_position["sl"] = new_sl
    _active_position["trail_level"] = trail_lvl
    save_trade_state()
    return (
        f"🔄 <b>TRAILING SL UPDATED</b>\n"
        f"• Side: <b>{side}</b>\n"
        f"• Price: <code>{current_price}</code>\n"
        f"• New SL: <code>{new_sl}</code>\n"
        f"• Trail level: <code>T{trail_lvl if trail_lvl else 'mid'}</code>"
    )




def close_bybit_position(reason="manual"):
    """Close current spot-margin BTC position at market."""
    global _active_position
    side = _active_position.get("side")
    if not side:
        return "ℹ️ Koi open trade nahi hai."
    session = get_bybit_session()
    close_side = "Sell" if side == "Buy" else "Buy"
    btc_qty = _active_position.get("btc_qty")
    notional = _active_position.get("notional")
    lev = str(int(_active_position.get("leverage") or user_trade_settings.get("leverage") or 10))
    last_err = None
    if btc_qty:
        try:
            session.place_order(
                category=BYBIT_CATEGORY,
                symbol=BYBIT_SYMBOL,
                side=close_side,
                orderType="Market",
                qty=f"{float(btc_qty):.3f}",
                timeInForce="GTC",
                reduceOnly=True,
                positionIdx=0,
            )
            old = dict(_active_position)
            _active_position = {"side": None, "entry": None, "qty": None, "symbol": "BTCUSDT",
                                "sl": None, "trail_level": 0, "mid_sl": None, "levels": None}
            return (
                f"🛑 <b>TRADE CLOSED</b> ({reason})\n"
                f"• Closed: <b>{close_side}</b> to exit {old.get('side')}\n"
                f"• Entry was: <code>{old.get('entry')}</code>\n"
                f"• Qty: <code>{btc_qty}</code> BTC"
            )
        except Exception as e:
            last_err = e
    if notional:
        try:
            session.place_order(
                category=BYBIT_CATEGORY,
                symbol=BYBIT_SYMBOL,
                side=close_side,
                orderType="Market",
                qty=f"{max(0.001, round(float(notional) / max(float(_active_position.get('entry') or 1), 1), 3)):.3f}",
                timeInForce="GTC",
                reduceOnly=True,
                positionIdx=0,
            )
            old = dict(_active_position)
            _active_position = {"side": None, "entry": None, "qty": None, "symbol": "BTCUSDT",
                                "sl": None, "trail_level": 0, "mid_sl": None, "levels": None}
            return (
                f"🛑 <b>TRADE CLOSED</b> ({reason})\n"
                f"• Closed: <b>{close_side}</b> to exit {old.get('side')}\n"
                f"• Entry was: <code>{old.get('entry')}</code>"
            )
        except Exception as e:
            last_err = e
    return f"❌ Close fail: <code>{last_err}</code>"


def manual_market_entry(side: str) -> str:
    """Enter at CURRENT price (UP=Buy, DOWN=Sell). Engine ON ki zarurat nahi."""
    df, src = fetch_data("BITCOIN (BTC)")
    if df is None:
        return "❌ BTC price fetch fail."
    lvl = calculate_op_levels(df, "BITCOIN (BTC)")
    px = get_bybit_btc_live_price() or float(lvl.get("current_price") or 0)
    msg = place_bybit_trade(side, px, lvl, user_trade_settings, force=True)
    if not msg:
        return "❌ Order nahi laga. /tradestatus check karo."
    return "📌 <b>MANUAL ENTRY @ CURRENT PRICE</b>\n" + msg


async def cmd_long_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    WAITING_MANUAL_LEV[update.effective_user.id] = {"side": "Buy", "amount": None}
    await update.message.reply_text(
        "⚡ <b>MANUAL BUY</b>\nPehle margin USDT bhejo (jaise 10):",
        parse_mode="HTML",
    )


async def cmd_short_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    WAITING_MANUAL_LEV[update.effective_user.id] = {"side": "Sell", "amount": None}
    await update.message.reply_text(
        "⚡ <b>MANUAL SELL</b>\nPehle margin USDT bhejo (jaise 10):",
        parse_mode="HTML",
    )


async def cmd_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(close_bybit_position("manual /close"), parse_mode="HTML")


async def cmd_now_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "📌 <b>Current price pe trade</b>\n"
        "Level miss ho jaye to yahan se lo:\n\n"
        "• /long — UP (Buy) abhi ke price pe\n"
        "• /short — DOWN (Sell) abhi ke price pe\n"
        "• /close — current open trade band\n"
        "• /tradestatus — engine + last trade\n\n"
        "Auto entry Confirm pe pehle jaisi rahegi.",
        parse_mode="HTML",
    )


async def start_trading_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ <b>Access Denied:</b> Yeh trading feature sirf Admin ke liye restricted hai!",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "💰 <b>Trading Amount Set Karein:</b>\n"
        "Kripya $1 se $20 ke beech apna margin amount enter karein (jaise: 5 ya 10):",
        parse_mode="HTML",
    )
    return AMOUNT


async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.strip())
        if not (1 <= amt <= 20):
            await update.message.reply_text(
                "⚠️ <b>Invalid Amount!</b> Kripya sirf $1 se $20 ke beech ki value dein.",
                parse_mode="HTML",
            )
            return AMOUNT
        context.user_data["temp_amount"] = amt
        await update.message.reply_text(
            f"✅ Amount Set: <b>${amt}</b>\n\n"
            "⚡ <b>Ab Leverage Enter Karein:</b>\n"
            "Kripya 1x se 100x ke beech leverage enter karein (jaise: 20):",
            parse_mode="HTML",
        )
        return LEVERAGE
    except ValueError:
        await update.message.reply_text("⚠️ Kripya ek valid number enter karein (jaise 5 ya 10).")
        return AMOUNT


async def receive_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_TRADING_ACTIVE, user_trade_settings
    try:
        lev = int(update.message.text.strip().replace("x", "").replace("X", ""))
        if not (1 <= lev <= 150):
            await update.message.reply_text(
                "⚠️ <b>Invalid Leverage!</b> 1x se 150x allowed hai.",
                parse_mode="HTML",
            )
            return LEVERAGE

        amt = float(context.user_data.get("temp_amount", 5))
        px = float(get_bybit_btc_live_price() or 83000)
        min_notional = 0.001 * px
        need_margin = min_notional / max(lev, 1)
        if amt * lev < min_notional - 1:
            await update.message.reply_text(
                f"⛔ <b>Size chhota</b>\n"
                f"Min qty 0.001 BTC ≈ <code>${min_notional:.0f}</code>\n"
                f"Tune: ${amt} × {lev}x = <code>${amt*lev:.0f}</code>\n"
                f"Kam se kam margin ≈ <code>${need_margin:.1f}</code> at {lev}x\n"
                f"Ya leverage badhao. Dobara number bhejo.",
                parse_mode="HTML",
            )
            return LEVERAGE
        user_trade_settings["amount"] = amt
        user_trade_settings["leverage"] = lev
        BOT_TRADING_ACTIVE = True
        save_trade_state()

        total_position = amt * lev
        keys_ok = bool(BYBIT_API_KEY and BYBIT_API_SECRET)
        await update.message.reply_text(
            "🟢 <b>BTC FUTURES BOT STARTED</b> <code>v29-calc</code>\n\n"
            f"• <b>Margin:</b> ${amt}\n"
            f"• <b>Leverage:</b> {lev}x\n"
            f"• <b>Total Position Size:</b> ${total_position}\n"
            f"• <b>Bybit API Keys:</b> {'✅ Set' if keys_ok else '❌ Missing (set BYBIT_API_KEY & SECRET)'}\n\n"
            "🚀 Bot ab <b>Buy Above Confirm</b> / <b>Sell Below Confirm</b> pe entry lega.\n"
            "• Initial SL = midpoint (b_conf + s_conf)/2\n"
            "• Trail: T2→T1 … T10→T9, above T10→T10 lock (SHORT same)\n\n"
            "Manual: /long  /short  /close\nBand (auto engine): /stoptrade",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Kripya 1 se 100 ke beech leverage enter karein (jaise 20).")
        return LEVERAGE


async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Setup cancel kar diya gaya hai.")
    return ConversationHandler.END


async def stop_trading_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_TRADING_ACTIVE
    if update.effective_user.id != ADMIN_ID:
        return
    BOT_TRADING_ACTIVE = False
    save_trade_state()
    await update.message.reply_text(
        "🛑 <b>Auto-trading STOPPED.</b> Naye orders nahi lagenge.",
        parse_mode="HTML",
    )


async def trade_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    load_trade_state()
    status = "🟢 ACTIVE" if BOT_TRADING_ACTIVE else "🔴 OFF"
    pos = _active_position
    extra = ""
    try:
        df, src = fetch_data("BITCOIN (BTC)")
        if df is not None:
            lvl = calculate_op_levels(df, "BITCOIN (BTC)")
            extra = (
                f"\n• Source: <code>{src}</code>\n"
                f"• Live/Mark: <code>{lvl.get('current_price')}</code>\n"
                f"• Day Open IST: <code>{lvl.get('day_open')}</code>\n"
                f"• Buy Confirm: <code>{lvl.get('b_conf')}</code>\n"
                f"• Sell Confirm: <code>{lvl.get('s_conf')}</code>\n"
                f"• Mid SL: <code>{round((float(lvl.get('b_conf',0))+float(lvl.get('s_conf',0)))/2,2)}</code>"
            )
    except Exception as e:
        extra = f"\n• Levels fetch error: <code>{e}</code>"
    await update.message.reply_text(
        f"📊 <b>Bybit Auto-Trade Status</b>\n"
        f"• Engine: {status}\n"
        f"• Margin: ${user_trade_settings.get('amount')}\n"
        f"• Leverage: {user_trade_settings.get('leverage')}x\n"
        f"• Last side: {pos.get('side')}\n"
        f"• Last entry: {pos.get('entry')}\n"
        f"• Qty: {pos.get('qty')}"
        + extra,
        parse_mode="HTML",
    )



def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    load_trade_state()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    start_target_monitor(app)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("performance", admin_performance))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("user", admin_user_activity))
    app.add_handler(CommandHandler("users", admin_users_list))
    app.add_handler(CommandHandler("addvip", admin_add_vip))
    app.add_handler(CommandHandler("removevip", admin_remove_vip))
    app.add_handler(CommandHandler("block", admin_block))
    app.add_handler(CommandHandler("unblock", admin_unblock))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("stoptrade", stop_trading_cmd))
    app.add_handler(CommandHandler("tradestatus", trade_status_cmd))
    app.add_handler(CommandHandler("long", cmd_long_now))
    app.add_handler(CommandHandler("short", cmd_short_now))
    app.add_handler(CommandHandler("close", cmd_close_now))
    app.add_handler(CommandHandler("now", cmd_now_help))
    app.add_handler(CommandHandler("manual", cmd_now_help))


    trade_conv = ConversationHandler(
        entry_points=[
            CommandHandler("trade", start_trading_flow),
            MessageHandler(filters.Regex("^⚙️ AUTO TRADE$"), start_trading_flow),
        ],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount)],
            LEVERAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_leverage)],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)],
    )
    app.add_handler(trade_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logging.info("Pro Bot is starting successfully with stable yfinance feed + Bybit auto-trade...")
    app.run_polling()

if __name__ == "__main__":
    main()

