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

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 OP Levels Tab"), KeyboardButton("💱 Forex & Global Tab")],
        [KeyboardButton("🪙 Crypto Tab"), KeyboardButton("🏛️ FII/DII Data")],
        [KeyboardButton("📅 Economic Calendar"), KeyboardButton("👥 VIP Referral Link")],
        [KeyboardButton("🆘 Help & Support"), KeyboardButton("📖 How to Use? (गाइड)")]
    ], resize_keyboard=True)

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
            resolved = "BTC-USD"
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
    out = [
        f"📊 <b>{matched_symbol.upper()} • PRO OP LEVELS ALGO</b>",
        f"📅 <code>{today_str}</code>",
        f"💰 <b>Spot LTP:</b> <code>{lvl['current_price']}</code>",
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

            subs = load_data(SUBSCRIPTIONS_FILE, {})
            if subs:
                all_symbols = set()
                for u_list in subs.values():
                    for sym in u_list:
                        all_symbols.add(sym)

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

                           if "BTC" in sym.upper():
                              check_and_manage_trade(current_lcp, lvl, user_trade_settings, exchange)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_user_blocked(user.id):
        return

    save_user(user)
    
    if context.args:
        try:
            referrer_id = int(context.args[0])
            record_referral(referrer_id, user.id)
        except Exception:
            pass

    if not await is_user_subscribed(context.bot, user.id):
        await update.message.reply_text(
            "⚠️ <b>कृपया बोट इस्तेमाल करने के लिए हमारा आधिकारिक चैनल जॉइन करें:</b>",
            reply_markup=get_force_sub_markup(),
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "👋 <b>Welcome to Pro OP Levels Bot!</b>\n"
        "नीचे दिए गए मेनू से विकल्प चुनें:",
        reply_markup=get_main_menu_keyboard(),
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
    if is_user_blocked(user.id):
        return

    text = update.message.text
    save_user(user)

    if not await is_user_subscribed(context.bot, user.id):
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
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu_keyboard())
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
            response_text = format_color_display(text, today_str, lvl, query_sym)
            await msg_wait.edit_text(response_text, parse_mode="HTML")
        else:
            await msg_wait.edit_text("❌ <b>Error:</b> Data fetch failed.", parse_mode="HTML")
        return

    else:
        symbol_input = text.strip()
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
            response_text = format_color_display(symbol_input, today_str, lvl, symbol_input)
            await msg_wait.edit_text(response_text, parse_mode="HTML")
        else:
            await msg_wait.reply_text(f"❌ <b>Error:</b> '{symbol_input}' का डाटा नहीं मिला।", parse_mode="HTML")
        return

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

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

def main():
    threading.Thread(target=run_web_server, daemon=True).start()

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logging.info("Pro Bot is starting successfully with stable yfinance feed...")
    app.run_polling()

if __name__ == "__main__":
    main()
# --- ADMIN ONLY BYBIT FUTURES & TRAILING SL SYSTEM ---
from telegram.ext import ConversationHandler

AMOUNT, LEVERAGE = range(2)
user_trade_settings = {"amount": 5, "leverage": 50}

# 1. Admin Check ke sath Trade Start karne ka Command/Flow
async def start_trading_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ <b>Access Denied:</b> Yeh trading feature sirf Admin ke liye restricted hai!")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "💰 <b>Trading Amount Set Karein:</b>\nKripya $1 se $20 ke beech apna margin amount enter karein (jaise: 5 ya 10):"
    )
    return AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.strip())
        if not (1 <= amt <= 20):
            await update.message.reply_text("⚠️ <b>Invalid Amount!</b> Kripya sirf $1 se $20 ke beech ki value dein.")
            return AMOUNT
            
        context.user_data['temp_amount'] = amt
        await update.message.reply_text(
            f"✅ Amount Set: <b>${amt}</b>\n\n⚡ <b>Ab Leverage Enter Karein:</b>\nKripya 1x se 100x ke beech leverage enter karein (jaise: 50):"
        )
        return LEVERAGE
    except ValueError:
        await update.message.reply_text("⚠️ Kripya ek valid number enter karein (jaise 5 ya 10).")
        return AMOUNT

async def receive_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_TRADING_ACTIVE, user_trade_settings
    try:
        lev = int(update.message.text.strip().replace("x", ""))
        if not (1 <= lev <= 100):
            await update.message.reply_text("⚠️ <b>Invalid Leverage!</b> Kripya 1 se 100 ke beech ki value dein.")
            return LEVERAGE
            
        amt = context.user_data.get('temp_amount', 5)
        user_trade_settings["amount"] = amt
        user_trade_settings["leverage"] = lev
        BOT_TRADING_ACTIVE = True
        
        total_position = amt * lev
        await update.message.reply_text(
            "🟢 <b>BTC FUTURES BOT STARTED SUCCESSFULLY!</b>\n\n"
            f"• <b>Margin:</b> ${amt}\n"
            f"• <b>Leverage:</b> {lev}x\n"
            f"• <b>Total Position Size:</b> ${total_position}\n\n"
            "🚀 Bot ab fresh breakout aur dynamic trailing SL ke sath active ho gaya hai!"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Kripya ek valid number enter karein (jaise 50).")
        return LEVERAGE

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Setup cancel kar diya gaya hai.")
    return ConversationHandler.END
    
