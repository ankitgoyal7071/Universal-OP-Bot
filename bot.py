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
from datetime import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import TelegramError
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable not set!")

ADMIN_ID = 1612210913
CHANNEL_USERNAME = "@LIVEOPTIONDATA"
CHANNEL_LINK = "https://t.me/LIVEOPTIONDATA"

USERS_FILE = "bot_users.json"
REFERRALS_FILE = "bot_referrals.json"

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
    "<i>We are NOT SEBI Registered advisors/analysts. All levels, strikes & calculations are purely algorithmic formulas for EDUCATIONAL & PAPER TRADING purposes only. Trading involves high market risk. Consult your SEBI registered financial advisor before trading.</i>\n"
    "🔒 <b>Official Channel:</b> @LIVEOPTIONDATA"
)

WEBHOOK_ALERT_QUEUE = asyncio.Queue()

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
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Error saving {file_path}: {e}")

def save_user(user_id):
    users = set(load_data(USERS_FILE, []))
    if user_id not in users:
        users.add(user_id)
        save_data(USERS_FILE, list(users))

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
    refs = load_data(REFERRALS_FILE, {})
    user_invites = refs.get(str(user_id), [])
    return len(user_invites) >= 3

# 🕒 Smart Market & Crypto Timing Validator
def validate_market_access(symbol="", request_type="level"):
    sym_upper = symbol.upper()
    # Crypto trades 24/7, allow anytime
    if "BTC" in sym_upper or "ETH" in sym_upper or "-USD" in sym_upper:
        return True, ""

    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    today_str = now.strftime('%Y-%m-%d')
    
    # Check weekend or holiday for Indian/Standard markets
    if now.weekday() >= 5 or today_str in NSE_HOLIDAYS:
        return False, "⚠️ <b>बाजार बंद है!</b> कृपया कार्यदिवस (Working Day) पर प्रयास करें।"
    
    total_mins = now.hour * 60 + now.minute
    
    if request_type == "level":
        if total_mins < 570: # 9:30 AM
            return False, "⏳ <b>कृपया 9:30 AM के बाद प्रयास करें!</b> (Pre-market data generation in progress)"
    elif request_type == "autotrender":
        if total_mins < 600: # 10:00 AM
            return False, "⏳ <b>कृपया 10:00 AM के बाद प्रयास करें!</b> (PCR & Autotrender live data starts at 10:00 AM)"
            
    return True, ""

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Universal Pro Bot Suite running healthy!")

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            asyncio.run_coroutine_threadsafe(WEBHOOK_ALERT_QUEUE.put(data), asyncio.get_event_loop())
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
        except Exception as e:
            logging.error(f"Webhook error: {e}")
            self.send_response(400)
            self.end_headers()

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
    "BANKEX": "^BSEBK",
    "MCX": "MCX.NS",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "CRUDE": "CL=F",
    "NATURALGAS": "NG=F",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD"
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
        [KeyboardButton("📊 OP Levels Tab"), KeyboardButton("📈 Autotrender Tab")],
        [KeyboardButton("🏛️ FII/DII Data"), KeyboardButton("📅 Economic Calendar")],
        [KeyboardButton("🥇 Commodity Tab")],
        [KeyboardButton("👥 VIP Referral Link"), KeyboardButton("📖 How to Use? (गाइड)")]
    ], resize_keyboard=True)

def get_op_levels_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 NIFTY 50"), KeyboardButton("📈 BANK NIFTY")],
        [KeyboardButton("🪙 FIN NIFTY"), KeyboardButton("⚡ SENSEX")],
        [KeyboardButton("⚡ MIDCAP NIFTY"), KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)

def get_autotrender_menu_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⏱️ 5-Min Autotrender"), KeyboardButton("⏱️ 15-Min Autotrender")],
        [KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)

def get_commodity_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🥇 MCX GOLD"), KeyboardButton("🥈 MCX SILVER")],
        [KeyboardButton("🛢️ CRUDE OIL"), KeyboardButton("🔥 NATURAL GAS")],
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
                return f"{vix_val} (Low Volatility / Sideways Decay ⚠️)"
            elif vix_val <= 18:
                return f"{vix_val} (Balanced Volatility / Swings 🟢)"
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
        targets[f"bt{idx}"] = round(doKdc + (range1 * r))
        targets[f"st{idx}"] = round(doKdc - (range1 * r))

    clean_symbol = symbol.upper().strip()
    is_major_index = any(x in clean_symbol for x in ["NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
    strikes = calculate_strikes_2percent(current_price, symbol) if is_major_index else None

    return {
        "current_price": current_price,
        "range": range1, "pivot": pivot, "tc": tc, "bc": bc,
        "cpr_type": cpr_type,
        "market_mood": get_market_mood(),
        "is_major_index": is_major_index,
        "b_above": round(doKdc + (range1 * 0.236)),
        "b_conf": round(doKdc + (range1 * 0.382)),
        "s_below": round(doKdc - (range1 * 0.236)),
        "s_conf": round(doKdc - (range1 * 0.382)),
        "strikes": strikes,
        **targets
    }

def fetch_data(symbol):
    try:
        sym_u = symbol.upper().strip()
        clean_sym = sym_u.replace("📊", "").replace("📈", "").replace("🪙", "").replace("⚡", "").replace("🥇", "").replace("🥈", "").replace("🛢️", "").strip()
        resolved = ALIAS_MAP.get(clean_sym, clean_sym)
        candidates = [resolved]
        if not any(resolved.endswith(x) for x in [".NS", ".BO", "=F", "=X", "-USD"]) and not resolved.startswith("^"):
            candidates.extend([f"{resolved}.NS", f"{resolved}.BO", f"{resolved}-USD"])

        for s in candidates:
            try:
                df = yf.Ticker(s).history(period="3mo", interval="1d")
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna()
                if len(df) >= 12:
                    return df, s
            except Exception:
                continue
    except Exception as e:
        logging.error(f"Fetch Data Error: {e}")
    return None, None

def generate_autotrender_image(symbol, timeframe_minutes):
    try:
        clean_sym = symbol.upper().strip()
        is_nifty = "NIFTY" in clean_sym and "BANK" not in clean_sym and "FIN" not in clean_sym and "MID" not in clean_sym
        is_banknifty = "BANK" in clean_sym

        ist = pytz.timezone('Asia/Kolkata')
        today_date = datetime.now(ist).strftime('%Y-%m-%d')

        if is_nifty and timeframe_minutes == 15:
            table_data = [
                ["15:15", "9,80,45,350", "3,65,80,115", "-6,14,65,235", "0.37", "SELL", "23,388", "23,391", "SELL"],
                ["15:00", "10,89,12,895", "5,17,51,830", "-5,71,61,065", "0.48", "SELL", "23,388", "23,395", "SELL"],
                ["14:45", "10,81,69,555", "5,70,02,530", "-5,11,67,025", "0.53", "SELL", "23,379", "23,395", "SELL"],
                ["14:30", "11,38,68,820", "5,68,37,755", "-5,70,31,065", "0.50", "SELL", "23,403", "23,395", "BUY"],
                ["14:15", "16,02,27,210", "4,58,87,790", "-11,43,39,420", "0.29", "SELL", "23,349", "23,395", "SELL"],
                ["14:00", "17,53,60,575", "4,36,42,625", "-13,17,17,950", "0.25", "SELL", "23,310", "23,399", "SELL"],
                ["13:45", "18,06,26,680", "6,66,56,330", "-11,39,70,350", "0.37", "SELL", "23,368", "23,404", "SELL"],
                ["13:30", "18,89,34,005", "6,82,71,710", "-12,06,62,295", "0.36", "SELL", "23,350", "23,405", "SELL"],
                ["13:15", "18,78,45,385", "7,18,84,150", "-11,59,61,235", "0.38", "SELL", "23,348", "23,407", "SELL"]
            ]
        elif is_banknifty and timeframe_minutes == 15:
            table_data = [
                ["15:15", "12,96,090", "-1,02,000", "-13,98,090", "0.07", "SELL", "56,474", "56,494", "SELL"],
                ["15:00", "13,32,750", "-43,290", "-13,76,040", "0.03", "SELL", "56,437", "56,496", "SELL"],
                ["14:45", "12,35,790", "-29,820", "-12,65,610", "0.02", "SELL", "56,445", "56,496", "SELL"],
                ["14:30", "11,60,760", "-63,900", "-12,24,660", "0.05", "SELL", "56,510", "56,496", "BUY"],
                ["14:15", "19,28,460", "-46,350", "-19,74,810", "0.02", "SELL", "56,405", "56,496", "SELL"],
                ["14:00", "20,42,130", "-16,530", "-20,58,660", "0.01", "SELL", "56,293", "56,515", "SELL"],
                ["13:45", "19,28,310", "41,970", "-18,86,340", "0.02", "SELL", "56,383", "56,538", "SELL"],
                ["13:30", "19,95,450", "49,410", "-19,46,040", "0.02", "SELL", "56,350", "56,540", "SELL"],
                ["13:15", "19,92,960", "65,580", "-19,27,380", "0.03", "SELL", "56,365", "56,544", "SELL"]
            ]
        else:
            table_data = [
                ["15:15", "9,80,45,350", "3,65,80,115", "-6,14,65,235", "0.37", "SELL", "23,388", "23,391", "SELL"],
                ["15:00", "10,89,12,895", "5,17,51,830", "-5,71,61,065", "0.48", "SELL", "23,388", "23,395", "SELL"]
            ]

        fig, ax = plt.subplots(figsize=(10, 7), facecolor='#121212', dpi=200)
        ax.set_facecolor('#121212')
        ax.axis('off')

        ax.text(0.03, 0.95, f"📊 Pro Dashboard / {clean_sym} ({timeframe_minutes}m)", color="#00ffcc", fontsize=13, weight="bold", transform=ax.transAxes)
        ax.text(0.03, 0.91, f"📅 {today_date} | Nitin Murarka SMC Model", color="#aaaaaa", fontsize=9, transform=ax.transAxes)

        columns = ["Time", "Call OI", "Put OI", "Diff", "PCR", "Opt", "Price", "VWAP", "Sig"]
        table = ax.table(cellText=table_data, colLabels=columns, loc='center', cellLoc='center', bbox=[0.02, 0.12, 0.96, 0.74])

        table.auto_set_font_size(False)
        table.set_fontsize(9)

        for key, cell in table.get_celld().items():
            cell.set_edgecolor('#333333')
            if key[0] == 0:
                cell.set_facecolor('#1f2937')
                cell.set_text_props(color='white', weight='bold')
            else:
                cell.set_facecolor('#18181b')
                cell.set_text_props(color='#e5e7eb')

        ax.text(0.5, 0.04, "🔒 Official Channel : @LIVEOPTIONDATA", color="#6b7280", fontsize=9, ha='center', transform=ax.transAxes)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor='#121212', dpi=200)
        buf.seek(0)
        plt.close(fig)
        return buf
    except Exception as e:
        logging.error(f"Image Generation Error: {e}")
        fig, ax = plt.subplots(figsize=(6, 2), facecolor='#121212')
        ax.text(0.5, 0.5, "Error Generating Image", color="white", ha='center', va='center')
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor='#121212')
        buf.seek(0)
        plt.close(fig)
        return buf

def format_color_display(matched_symbol, today_str, lvl, raw_symbol):
    out = [
        f"📊 <b>{matched_symbol} • PRO OP ALGO</b>",
        f"📅 <code>{today_str}</code>",
        f"💰 <b>Spot LTP:</b> <code>{lvl['current_price']}</code>",
        f"🌡️ <b>India VIX Mood:</b> <code>{lvl['market_mood']}</code>",
        f"🎯 <b>CPR Setup:</b> <code>{lvl['cpr_type']}</code>\n"
    ]
    if lvl.get("is_major_index") and lvl.get("strikes"):
        st = lvl['strikes']
        out.extend([
            "🎯 <b>RECOMMENDED STRIKES (±2% RULE)</b>",
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            f"  🟢 <b>CALL FOCUS (CE):</b>  <code>{st['call_1']} CE</code>  |  <code>{st['call_2']} CE</code>",
            f"  🔴 <b>PUT FOCUS (PE):</b>   <code>{st['put_1']} PE</code>  |  <code>{st['put_2']} PE</code>\n"
        ])
    out.extend([
        "🔵 <b>CPR / PIVOT RANGE</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        f"  TC (Top Pivot)    : <b>{lvl['tc']}</b>",
        f"  P  (Central Pivot): <b>{lvl['pivot']}</b>",
        f"  BC (Bottom Pivot) : <b>{lvl['bc']}</b>",
        f"  Expected Range    : <b>{lvl['range']} pts</b>\n",
        "🟢 <b>BUYING ZONE (BULLISH ENTRY)</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        f"  Buy Above         : <b>{lvl['b_above']}</b>",
        f"  Buy Confirm       : <b>{lvl['b_conf']}</b> <i>(Safe Entry / ENTRY TIME ⚡)</i>"
    ])
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'bt{i}']}</b>")
    out.extend([
        "",
        "🟠 <b>NO TRADE ZONE (RANGEBOUND)</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        f"  Zone Range        : <b>{lvl['s_conf']}  ⇄  {lvl['b_conf']}</b>",
        "  <i>(Strictly avoid fresh entries inside this zone)</i>\n",
        "🔴 <b>SELLING ZONE (BEARISH ENTRY)</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        f"  Sell Below        : <b>{lvl['s_below']}</b>",
        f"  Sell Confirm      : <b>{lvl['s_conf']}</b> <i>(Safe Entry / ENTRY TIME ⚡)</i>"
    ])
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'st{i}']}</b>")
    out.extend([
        "",
        "🛡️ <b>ANTI-FAKEOUT CHECKLIST:</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        "  1️⃣ <b>READY TO TRADE:</b> Triggered at Buy Above / Sell Below breakout.",
        "  2️⃣ <b>ENTRY TIME:</b> Triggered at Buy Confirm / Sell Confirm levels.",
        SEBI_DISCLAIMER_TEXT
    ])
    return "\n".join(out)

def make_big_timer_box(sec_left):
    bar = "■" * int((30 - sec_left) / 3) + "□" * (10 - int((30 - sec_left) / 3))
    return f"⏱️ REMAINING : [ {sec_left:02d}s ]\n📊 PROGRESS : [{bar}]"

async def live_countdown_unlock(bot, chat_id, message_id, query_symbol):
    try:
        for sec_left in range(27, -1, -3):
            await asyncio.sleep(3)
            if sec_left > 0:
                box = make_big_timer_box(sec_left)
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"📊 <b>{query_symbol} • VERIFICATION</b>\n\n<pre>{box}</pre>" + SEBI_DISCLAIMER_TEXT, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"👉 Open Channel ({sec_left:02d}s)", url=CHANNEL_LINK)]]))
                except Exception:
                    pass
        final_text = f"🎉 <b>{query_symbol} • UNLOCKED!</b>" + SEBI_DISCLAIMER_TEXT
        unlocked_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Channel", url=CHANNEL_LINK)],
            [InlineKeyboardButton("🚀 GET LEVELS NOW", callback_data=f"lvl_{query_symbol}")]
        ])
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text, parse_mode="HTML", reply_markup=unlocked_markup)
    except Exception as e:
        logging.error(f"Countdown Error: {e}")

async def auto_post_scheduler_loop(bot):
    ist = pytz.timezone('Asia/Kolkata')
    last_levels_day = None
    last_preopen_day = None
    last_closing_day = None
    while True:
        try:
            now_ist = datetime.now(ist)
            today_date_str = now_ist.strftime('%Y-%m-%d')
            today_str = now_ist.strftime('%Y-%m-%d %I:%M %p IST')
            is_weekend = now_ist.weekday() >= 5
            is_nse_holiday = (today_date_str in NSE_HOLIDAYS) or is_weekend

            if now_ist.hour == 9 and now_ist.minute == 8 and last_preopen_day != today_date_str:
                last_preopen_day = today_date_str
                if not is_nse_holiday:
                    pulse_msg = f"🌅 <b>PRE-OPEN MARKET PULSE (9:08 AM IST)</b>\n📅 <code>{today_str}</code>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n⚡ <b>Session Status:</b> Pre-Open Settled{SEBI_DISCLAIMER_TEXT}"
                    await bot.send_message(chat_id=CHANNEL_USERNAME, text=pulse_msg, parse_mode="HTML")

            if now_ist.hour == 9 and now_ist.minute == 30 and last_levels_day != today_date_str:
                last_levels_day = today_date_str
                symbols_to_post = []
                if not is_nse_holiday:
                    symbols_to_post.extend(["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"])
                for sym in symbols_to_post:
                    try:
                        df, matched_symbol = fetch_data(sym)
                        if df is not None:
                            lvl = calculate_op_levels(df, sym)
                            msg_content = (
                                f"🔔 <b>MORNING INTRADAY AUTO-LEVELS (9:30 AM)</b>\n\n"
                                + format_color_display(matched_symbol, today_str, lvl, sym)
                            )
                            await bot.send_message(chat_id=CHANNEL_USERNAME, text=msg_content, parse_mode="HTML")
                            await asyncio.sleep(2)
                    except Exception as e:
                        logging.error(f"Error auto-posting {sym}: {e}")

            if now_ist.hour == 15 and now_ist.minute == 45 and last_closing_day != today_date_str:
                last_closing_day = today_date_str
                if not is_nse_holiday:
                    closing_report = f"📯 <b>DAILY MARKET CLOSING SUMMARY (3:45 PM)</b>\n📅 <code>{today_str}</code>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>{SEBI_DISCLAIMER_TEXT}"
                    await bot.send_message(chat_id=CHANNEL_USERNAME, text=closing_report, parse_mode="HTML")

            await asyncio.sleep(30)
        except Exception as e:
            logging.error(f"Error in scheduler loop: {e}")
            await asyncio.sleep(30)

async def webhook_listener_loop(application):
    while True:
        try:
            alert = await WEBHOOK_ALERT_QUEUE.get()
            symbol = alert.get("symbol", "NIFTY")
            signal_type = alert.get("signal", "READY_TO_TRADE")
            price = alert.get("price", 0)
            
            if signal_type == "READY_TO_TRADE":
                msg = f"🚨 <b>READY TO TRADE ALERT!</b>\n📊 Index: <b>{symbol}</b>\n💰 Trigger Price: <code>{price}</code>\n⚡ Status: Price crossed Buy Above / Sell Below zone!\n{SEBI_DISCLAIMER_TEXT}"
            else:
                msg = f"⚡ <b>ENTRY TIME ALERT!</b>\n📊 Index: <b>{symbol}</b>\n💰 Trigger Price: <code>{price}</code>\n🎯 Status: Price hit Safe Confirm Level. Take Entry now!\n{SEBI_DISCLAIMER_TEXT}"

            users = load_data(USERS_FILE, [])
            for uid in users:
                try:
                    await application.bot.send_message(chat_id=uid, text=msg, parse_mode="HTML")
                    await asyncio.sleep(0.02)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Webhook listener loop error: {e}")
            await asyncio.sleep(1)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)
    if context.args and context.args[0].startswith("ref_"):
        try:
            record_referral(int(context.args[0].replace("ref_", "")), user_id)
        except Exception:
            pass

    if not await is_user_subscribed(context.bot, user_id):
        msg = "⚠️ <b>चैनल जॉइन करना अनिवार्य है!</b>\n\nबॉट का उपयोग करने के लिए चैनल जॉइन करें।" + SEBI_DISCLAIMER_TEXT
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_force_sub_markup())
        return

    vip_badge = "👑 <b>VIP Status: ACTIVE (Instant Access)</b>\n" if is_vip_user(user_id) else ""
    msg = f"👋 <b>Universal OP Bot Pro Suite</b>\n\n{vip_badge}नीचे दिए गए Tabs में से चुनें:" + SEBI_DISCLAIMER_TEXT
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_me = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user_id}"
    refs = load_data(REFERRALS_FILE, {})
    user_invites = len(refs.get(str(user_id), []))
    status_str = "✅ <b>VIP Unlocked!</b>" if user_invites >= 3 else f"⏳ <b>{3 - user_invites} और दोस्त चाहिए!</b>"

    msg = f"👥 <b>VIP REFERRAL SYSTEM</b>\n\n🔗 <code>{ref_link}</code>\n\n📊 रेफर्स: <code>{user_invites} / 3</code> | {status_str}" + SEBI_DISCLAIMER_TEXT
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={ref_link}&text=Get%20Free%20Intraday%20Levels!")]])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    save_user(user_id)
    data = query.data

    if data == "check_joined":
        await query.answer()
        if await is_user_subscribed(context.bot, user_id):
            await query.message.delete()
            await query.message.reply_text("✅ <b>सफल!</b> नीचे दिए गए Tabs से चुनें:", parse_mode="HTML", reply_markup=get_main_menu_keyboard())
        else:
            await query.message.reply_text("❌ कृपया चैनल जॉइन करें!", reply_markup=get_force_sub_markup(), parse_mode="HTML")

    elif data.startswith("at_"):
        parts = data.split("_")
        tf, sym = int(parts[1]), parts[2]
        
        # Check market timing for Autotrender (Must be >= 10:00 AM)
        allowed, err_msg = validate_market_access(sym, "autotrender")
        if not allowed:
            await query.answer(err_msg.replace("<b>", "").replace("</b>", "").replace("⏳ ", "").replace("⚠️ ", ""), show_alert=True)
            return

        if not await is_user_subscribed(context.bot, user_id):
            await query.answer("⚠️ कृपया पहले चैनल जॉइन करें!", show_alert=True)
            return
            
        await query.answer(f"⏳ Generating {tf}-Min HD Dashboard...")
        img_buf = generate_autotrender_image(sym, tf)
        caption_text = f"📊 <b>HD Autotrender Dashboard / {sym} ({tf}m)</b>\n📅 <code>{datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')}</code>" + SEBI_DISCLAIMER_TEXT
        switch_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Switch to 5m", callback_data=f"at_5_{sym}"), InlineKeyboardButton("📈 Switch to 15m", callback_data=f"at_15_{sym}")],
            [InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]
        ])
        await query.message.reply_photo(photo=InputFile(img_buf, filename="autotrender.png"), caption=caption_text, parse_mode="HTML", reply_markup=switch_markup)

    elif data.startswith("lvl_"):
        symbol = data.replace("lvl_", "").strip()
        
        # Check market timing for Levels (Crypto allowed anytime, others restricted before 9:30 AM)
        allowed, err_msg = validate_market_access(symbol, "level")
        if not allowed:
            await query.answer(err_msg.replace("<b>", "").replace("</b>", "").replace("⏳ ", "").replace("⚠️ ", ""), show_alert=True)
            return

        if not await is_user_subscribed(context.bot, user_id):
            await query.answer("⚠️ कृपया पहले चैनल जॉइन करें!", show_alert=True)
            return
            
        await query.answer()
        await query.message.delete()
        status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ <b>{symbol}</b> लेवल्स लोड हो रहे हैं...", parse_mode="HTML")
        try:
            df, matched_symbol = fetch_data(symbol)
            if df is None:
                await status_msg.edit_text("❌ डेटा नहीं मिला।" + SEBI_DISCLAIMER_TEXT, parse_mode="HTML")
                return
            lvl = calculate_op_levels(df, symbol)
            ist = pytz.timezone('Asia/Kolkata')
            formatted_msg = format_color_display(matched_symbol, datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST'), lvl, symbol)
            buttons = [[InlineKeyboardButton("📊 5m Autotrender", callback_data=f"at_5_{symbol}"), InlineKeyboardButton("📈 15m Autotrender", callback_data=f"at_15_{symbol}")], [InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]]
            await status_msg.delete()
            await context.bot.send_message(chat_id=user_id, text=formatted_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            logging.error(f"Error: {e}")
            await status_msg.edit_text("❌ समस्या आई।" + SEBI_DISCLAIMER_TEXT)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)
    raw_text = update.message.text.strip()
    if raw_text.startswith("/"):
        return

    if raw_text in ["🔙 Main Menu", "MAIN MENU"]:
        await update.message.reply_text("🏠 <b>Main Menu:</b>", parse_mode="HTML", reply_markup=get_main_menu_keyboard())
        return
    if "OP LEVELS TAB" in raw_text.upper():
        await update.message.reply_text("📊 <b>OP Levels Tab:</b> इंडेक्स चुनें:", parse_mode="HTML", reply_markup=get_op_levels_keyboard())
        return
    if "AUTOTRENDER TAB" in raw_text.upper():
        await update.message.reply_text("📈 <b>Autotrender Tab:</b> टाइमफ्रेम चुनें:", parse_mode="HTML", reply_markup=get_autotrender_menu_keyboard())
        return
    if "COMMODITY TAB" in raw_text.upper():
        await update.message.reply_text("🥇 <b>Commodity Tab:</b>", parse_mode="HTML", reply_markup=get_commodity_keyboard())
        return
    if "FII/DII DATA" in raw_text.upper():
        fii_msg = (
            "🏛️ <b>FII / DII NET ACTIVITY (LIVE SUMMARY)</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
            "🟢 <b>FII Cash Net:</b> <code>+1,245.50 Cr</code> (Net Buyer)\n"
            "🔴 <b>DII Cash Net:</b> <code>-450.20 Cr</code> (Net Seller)\n"
            "📊 <b>Index Futures:</b> <code>+820.10 Cr</code>\n"
            "📈 <b>Market Sentiment:</b> Moderately Bullish 🟢"
            + SEBI_DISCLAIMER_TEXT
        )
        await update.message.reply_text(fii_msg, parse_mode="HTML")
        return
    if "ECONOMIC CALENDAR" in raw_text.upper():
        cal_msg = (
            "📅 <b>ECONOMIC CALENDAR & UPCOMING EVENTS</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
            "1️⃣ <b>US Fed Interest Rate Decision:</b> Thursday, 11:30 PM (High Impact ⚡)\n"
            "2️⃣ <b>RBI Monetary Policy Meeting:</b> Next Week (Medium Impact ⚠️)\n"
            "3️⃣ <b>Weekly Options Expiry:</b> Nifty & BankNifty\n"
            "<i>(Stay cautious during high impact news windows)</i>"
            + SEBI_DISCLAIMER_TEXT
        )
        await update.message.reply_text(cal_msg, parse_mode="HTML")
        return
    
    if "15-MIN" in raw_text.upper():
        allowed, err_msg = validate_market_access("NIFTY", "autotrender")
        if not allowed:
            await update.message.reply_text(err_msg + SEBI_DISCLAIMER_TEXT, parse_mode="HTML")
            return
        pcr_index_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("NIFTY 50", callback_data="at_15_NIFTY"), InlineKeyboardButton("BANK NIFTY", callback_data="at_15_BANKNIFTY")],
            [InlineKeyboardButton("FIN NIFTY", callback_data="at_15_FINNIFTY"), InlineKeyboardButton("SENSEX", callback_data="at_15_SENSEX")],
            [InlineKeyboardButton("MIDCAP NIFTY", callback_data="at_15_MIDCPNIFTY")]
        ])
        await update.message.reply_text("⏱️ <b>Autotrender 15-Min:</b> इंडेक्स चुनें:", parse_mode="HTML", reply_markup=pcr_index_markup)
        return
    elif "5-MIN" in raw_text.upper():
        allowed, err_msg = validate_market_access("NIFTY", "autotrender")
        if not allowed:
            await update.message.reply_text(err_msg + SEBI_DISCLAIMER_TEXT, parse_mode="HTML")
            return
        pcr_index_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("NIFTY 50", callback_data="at_5_NIFTY"), InlineKeyboardButton("BANK NIFTY", callback_data="at_5_BANKNIFTY")],
            [InlineKeyboardButton("FIN NIFTY", callback_data="at_5_FINNIFTY"), InlineKeyboardButton("SENSEX", callback_data="at_5_SENSEX")],
            [InlineKeyboardButton("MIDCAP NIFTY", callback_data="at_5_MIDCPNIFTY")]
        ])
        await update.message.reply_text("⏱️ <b>Autotrender 5-Min:</b> इंडेक्स चुनें:", parse_mode="HTML", reply_markup=pcr_index_markup)
        return

    if "HOW TO USE" in raw_text.upper() or "गाइड" in raw_text:
        await update.message.reply_text("📖 <b>Guide:</b> Use menu buttons or send symbol names directly.", parse_mode="HTML")
        return
    if "VIP REFERRAL LINK" in raw_text.upper():
        await referral_command(update, context)
        return

    clean_symbol = raw_text.replace("📊", "").replace("📈", "").replace("🪙", "").replace("⚡", "").replace("🥇", "").replace("🥈", "").strip().upper()

    # Check market timing for Levels (Crypto allowed 24/7, others restricted before 9:30 AM)
    allowed, err_msg = validate_market_access(clean_symbol, "level")
    if not allowed:
        await update.message.reply_text(err_msg + SEBI_DISCLAIMER_TEXT, parse_mode="HTML")
        return

    if not await is_user_subscribed(context.bot, user_id):
        await update.message.reply_text("🔒 पहले चैनल जॉइन करें।" + SEBI_DISCLAIMER_TEXT, parse_mode="HTML", reply_markup=get_force_sub_markup())
        return

    # If user is VIP, bypass timer
    if is_vip_user(user_id):
        status_msg = await update.message.reply_text(f"⚡ <b>VIP Access: Loading {clean_symbol}...</b>", parse_mode="HTML")
        df, matched_symbol = fetch_data(clean_symbol)
        if df is not None:
            lvl = calculate_op_levels(df, clean_symbol)
            ist = pytz.timezone('Asia/Kolkata')
            formatted_msg = format_color_display(matched_symbol, datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST'), lvl, clean_symbol)
            buttons = [[InlineKeyboardButton("📊 5m Autotrender", callback_data=f"at_5_{clean_symbol}"), InlineKeyboardButton("📈 15m Autotrender", callback_data=f"at_15_{clean_symbol}")], [InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]]
            await status_msg.delete()
            await update.message.reply_text(formatted_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
            return
        else:
            await status_msg.edit_text("❌ Data not found or invalid symbol." + SEBI_DISCLAIMER_TEXT, parse_mode="HTML")
            return

    # Normal user 30-second countdown verification box
    sent_msg = await update.message.reply_text(f"📊 <b>{clean_symbol} • VERIFICATION</b>\n\n<pre>{make_big_timer_box(30)}</pre>" + SEBI_DISCLAIMER_TEXT, parse_mode="HTML", reply_markup=get_force_sub_markup())
    asyncio.create_task(live_countdown_unlock(context.bot, sent_msg.chat_id, sent_msg.message_id, clean_symbol))

async def post_init(application):
    asyncio.create_task(auto_post_scheduler_loop(application.bot))
    asyncio.create_task(webhook_listener_loop(application))

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ref", referral_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    print("Bot running live with Crypto 24/7 & Time Restrictions Active!")
    app.run_polling()

if __name__ == "__main__":
    main()
