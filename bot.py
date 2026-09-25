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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

def validate_market_access(symbol=""):
    sym_upper = symbol.upper()
    if "BTC" in sym_upper or "ETH" in sym_upper or "BITCOIN" in sym_upper or "ETHEREUM" in sym_upper or "-USD" in sym_upper:
        return True, ""

    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    today_str = now.strftime('%Y-%m-%d')
    
    if now.weekday() >= 5 or today_str in NSE_HOLIDAYS:
        return False, "⚠️ <b>बाजार बंद है!</b> कृपया कार्यदिवस (Working Day) पर प्रयास करें।"
    
    total_mins = now.hour * 60 + now.minute
    if total_mins < 570:
        return False, "⏳ <b>कृपया 9:30 AM के बाद प्रयास करें!</b> (Pre-market data generation in progress)"
            
    return True, ""

def fetch_fii_dii_data():
    try:
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
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
        "• <i>Impact:</i> Dollar Index (DXY), MCX Gold & Silver\n\n"
        "🛢️ <b>5. US EIA Crude Oil Inventories:</b>\n"
        "• <i>Impact:</i> MCX Crude Oil, Energy Stocks"
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
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "CRUDE": "CL=F",
    "NATURALGAS": "NG=F",
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
        [KeyboardButton("📊 OP Levels Tab"), KeyboardButton("🏛️ FII/DII Data")],
        [KeyboardButton("📅 Economic Calendar"), KeyboardButton("🥇 Commodity Tab")],
        [KeyboardButton("🪙 Crypto Tab"), KeyboardButton("👥 VIP Referral Link")],
        [KeyboardButton("📖 How to Use? (गाइड)")]
    ], resize_keyboard=True)

def get_op_levels_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 NIFTY 50"), KeyboardButton("📈 BANK NIFTY")],
        [KeyboardButton("🪙 FIN NIFTY"), KeyboardButton("⚡ SENSEX")],
        [KeyboardButton("⚡ MIDCAP NIFTY"), KeyboardButton("🔙 Main Menu")]
    ], resize_keyboard=True)

def get_commodity_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🥇 MCX GOLD"), KeyboardButton("🥈 MCX SILVER")],
        [KeyboardButton("🛢️ CRUDE OIL"), KeyboardButton("🔥 NATURAL GAS")],
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
        clean_sym = sym_u.replace("📊", "").replace("📈", "").replace("🪙", "").replace("⚡", "").replace("🥇", "").replace("🥈", "").replace("🛢️", "").replace("🔥", "").strip()
        
        # स्मार्ट मैपिंग ताकि कमोडिटी और क्रिप्टो के नाम सीधे कैच हो जाएं
        if "GOLD" in clean_sym:
            clean_sym = "GOLD"
        elif "SILVER" in clean_sym:
            clean_sym = "SILVER"
        elif "CRUDE" in clean_sym:
            clean_sym = "CRUDE"
        elif "NATURAL" in clean_sym:
            clean_sym = "NATURALGAS"
        elif "BITCOIN" in clean_sym or clean_sym == "BTC":
            clean_sym = "BTC"
        elif "ETHEREUM" in clean_sym or clean_sym == "ETH":
            clean_sym = "ETH"

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)
    
    if context.args:
        try:
            referrer_id = int(context.args[0])
            record_referral(referrer_id, user_id)
        except Exception:
            pass

    if not await is_user_subscribed(context.bot, user_id):
        await update.message.reply_text(
            "⚠️ <b>कृपया बोट इस्तेमाल करने के लिए हमारा आधिकारिक चैनल जॉइन करें:</b>",
            reply_markup=get_force_sub_markup(),
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "👋 <b>Welcome to Pro OP Levels Bot!</b>\n"
        "नीचे दिए गए मेनू से विकल्प चुनें या स्टॉक/क्रिप्टो नाम (जैसे RELIANCE, BTC, ETH) टाइप करें:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode="HTML"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    save_user(user_id)

    if not await is_user_subscribed(context.bot, user_id):
        await update.message.reply_text(
            "⚠️ कृपया पहले चैनल जॉइन करें:",
            reply_markup=get_force_sub_markup(),
            parse_mode="HTML"
        )
        return

    if text == "🔙 Main Menu":
        await update.message.reply_text("Main Menu:", reply_markup=get_main_menu_keyboard())
        return

    elif text == "📊 OP Levels Tab":
        await update.message.reply_text("सूचकांक (Index) चुनें:", reply_markup=get_op_levels_keyboard())
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

    elif text == "🥇 Commodity Tab":
        await update.message.reply_text("कमोडिटी चुनें:", reply_markup=get_commodity_keyboard())
        return

    elif text == "👥 VIP Referral Link":
        bot_user = context.bot.username
        ref_link = f"https://t.me/{bot_user}?start={user_id}"
        refs = load_data(REFERRALS_FILE, {})
        invites_count = len(refs.get(str(user_id), []))
        vip_status = "🟢 VIP ACTIVE" if is_vip_user(user_id) else "🔴 FREE USER"
        
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
            "1. <b>OP Levels / Stocks:</b> Nifty/BankNifty या कोई भी स्टॉक (जैसे RELIANCE, TCS) सीधे टाइप करें।\n"
            "2. <b>🪙 Crypto Tab:</b> Bitcoin (BTC) और Ethereum (ETH) के 24/7 लेवल्स चेक करें।\n"
            "3. <b>🏛️ FII/DII Data:</b> Check institutional cash activity.\n"
            "4. <b>📅 Economic Calendar:</b> Check macro events.\n"
            "5. <b>🥇 Commodity Tab:</b> Gold, Silver, Crude, Natural Gas."
        )
        await update.message.reply_text(guide_text, parse_mode="HTML")
        return

    elif text in ["📊 NIFTY 50", "📈 BANK NIFTY", "🪙 FIN NIFTY", "⚡ SENSEX", "⚡ MIDCAP NIFTY", 
                  "🥇 MCX GOLD", "🥈 MCX SILVER", "🛢️ CRUDE OIL", "🔥 NATURAL GAS",
                  "🪙 BITCOIN (BTC)", "🪙 ETHEREUM (ETH)"]:
        
        query_sym = text
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
            await msg_wait.edit_text("❌ <b>Error:</b> Data fetch failed. Please try again later.", parse_mode="HTML")
        return

    else:
        symbol_input = text.strip()
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
            await msg_wait.edit_text(f"❌ <b>Error:</b> '{symbol_input}' का डाटा नहीं मिला। सही सिंबल (जैसे RELIANCE, BTC, ETH) टाइप करें।", parse_mode="HTML")
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logging.info("Pro Bot is starting successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
