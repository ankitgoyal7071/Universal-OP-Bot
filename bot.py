import os
import threading
import asyncio
import json
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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8868136868:AAGcUhj4to9fDi_SsADjDef1vlZt2nfDLM0")
ADMIN_ID = 1612210913

CHANNEL_USERNAME = "@LIVEOPTIONDATA"
CHANNEL_LINK = "https://t.me/LIVEOPTIONDATA"

MANDATORY_VISIT_SECONDS = 30
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

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Universal Pro Bot is running healthy!")

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
    "MCX GOLD": "GC=F",
    "SILVER": "SI=F",
    "MCX SILVER": "SI=F",
    "CRUDE": "CL=F",
    "CRUDE OIL": "CL=F",
    "CRUDEOIL": "CL=F",
    "NATURALGAS": "NG=F",
    "COPPER": "HG=F",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "USDINR": "USDINR=X",
    "GOLDBEES": "GOLDBEES.NS",
    "SILVERBEES": "SILVERBEES.NS"
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def is_user_subscribed(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except TelegramError as e:
        logging.error(f"Force Sub Error: {e}")
        return False

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("📊 NIFTY 50"), KeyboardButton("📈 BANK NIFTY")],
        [KeyboardButton("🪙 FIN NIFTY"), KeyboardButton("⚡ SENSEX")],
        [KeyboardButton("🥇 MCX GOLD"), KeyboardButton("🛢️ CRUDE OIL")],
        [KeyboardButton("🧮 Lot Size Calculator"), KeyboardButton("👥 VIP Referral Link")],
        [KeyboardButton("📖 How to Use? (गाइड)"), KeyboardButton("📢 Official Channel")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_force_sub_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel / चैनल जॉइन करें", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 Verify Joined / वेरिफाई करें", callback_data="check_joined")]
    ])

def calculate_strikes_2percent(spot_price, symbol):
    # Step size selection based on index
    sym_u = symbol.upper()
    if "BANK" in sym_u or "SENSEX" in sym_u:
        step = 100
    elif "FIN" in sym_u or "NIFTY" in sym_u or "MID" in sym_u:
        step = 50
    else:
        step = 50

    call_raw = spot_price - (spot_price * 0.02)
    put_raw = spot_price + (spot_price * 0.02)

    call_strike_1 = round(call_raw / step) * step
    call_strike_2 = call_strike_1 + step

    put_strike_1 = round(put_raw / step) * step
    put_strike_2 = put_strike_1 - step

    return {
        "call_1": int(call_strike_1),
        "call_2": int(call_strike_2),
        "put_1": int(put_strike_1),
        "put_2": int(put_strike_2)
    }

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

    if abs(dop - dc) > (0.382 * range11):
        doKdc = dop
    else:
        doKdc = dc

    logg = np.log(doKdc / closes[-2])
    adjusted_logs = [logg] + log_returns[:-1]
    avg_logg = sum(adjusted_logs) / 10.0
    sq_sum = sum([x**2 for x in adjusted_logs]) / 10.0
    volatility = np.sqrt(max(0, sq_sum - (avg_logg**2)))
    range1 = round(doKdc * volatility)

    pivot = round((dhigh + dlow + dc) / 3)
    bc = round((dhigh + dlow) / 2)
    tc = round((pivot - bc) + pivot)

    cpr_width = abs(tc - bc)
    cpr_percent = (cpr_width / pivot) * 100 if pivot else 0
    if cpr_percent < 0.25:
        cpr_type = "⚡ Narrow CPR (Trending Move Expected)"
    elif cpr_percent > 0.65:
        cpr_type = "⚠️ Wide CPR (Rangebound / Sideways Expected)"
    else:
        cpr_type = "⚖️ Normal CPR (Moderate Trending Day)"

    targets = {}
    ratios = [0.500, 0.618, 0.786, 1.000, 1.272, 1.618, 2.000, 2.272, 2.618, 3.000]
    for idx, r in enumerate(ratios, 1):
        targets[f"bt{idx}"] = round(doKdc + (range1 * r))
        targets[f"st{idx}"] = round(doKdc - (range1 * r))

    strikes = calculate_strikes_2percent(current_price, symbol)

    return {
        "current_price": current_price,
        "range": range1, "pivot": pivot, "tc": tc, "bc": bc,
        "cpr_type": cpr_type,
        "b_above": round(doKdc + (range1 * 0.236)),
        "b_conf": round(doKdc + (range1 * 0.382)),
        "s_below": round(doKdc - (range1 * 0.236)),
        "s_conf": round(doKdc - (range1 * 0.382)),
        "strikes": strikes,
        **targets
    }

def get_mcx_parity(symbol_name):
    target_ticker = "GC=F" if symbol_name == "GOLDMCX" else "SI=F"
    df_com = yf.Ticker(target_ticker).history(period="1mo", interval="1d")
    df_fx = yf.Ticker("USDINR=X").history(period="1mo", interval="1d")

    if df_com.empty or df_fx.empty:
        return None

    fx = float(df_fx['Close'].iloc[-1])
    multiplier = (10.0 / 31.1035) * fx * 1.06 if symbol_name == "GOLDMCX" else (1000.0 / 31.1035) * fx * 1.06

    res = pd.DataFrame(index=df_com.index)
    res['Open'] = df_com['Open'] * multiplier
    res['High'] = df_com['High'] * multiplier
    res['Low'] = df_com['Low'] * multiplier
    res['Close'] = df_com['Close'] * multiplier
    return res

def fetch_data(symbol):
    sym_u = symbol.upper().strip()
    if sym_u in ["GOLDMCX", "MCXGOLD", "MCX GOLD", "🥇 MCX GOLD"]:
        df = get_mcx_parity("GOLDMCX")
        return df, "MCX GOLD (10g / INR)"
    elif sym_u in ["SILVERMCX", "MCXSILVER", "MCX SILVER"]:
        df = get_mcx_parity("SILVERMCX")
        return df, "MCX SILVER (1kg / INR)"

    clean_sym = sym_u.replace("📊", "").replace("📈", "").replace("🪙", "").replace("⚡", "").replace("🛢️", "").strip()
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
    return None, None

def format_color_display(matched_symbol, today_str, lvl):
    st = lvl['strikes']
    out = []
    out.append(f"📊 <b>{matched_symbol} • PRO OP ALGO</b>")
    out.append(f"📅 <code>{today_str}</code>")
    out.append(f"💰 <b>Spot LTP:</b> <code>{lvl['current_price']}</code>")
    out.append(f"🎯 <b>CPR Setup:</b> <code>{lvl['cpr_type']}</code>\n")

    # 2% Strike Selection Segment
    out.append("🎯 <b>RECOMMENDED STRIKES (±2% RULE)</b>")
    out.append("<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>")
    out.append(f"  🟢 <b>CALL FOCUS (CE):</b>  <code>{st['call_1']} CE</code>  |  <code>{st['call_2']} CE</code>")
    out.append(f"  🔴 <b>PUT FOCUS (PE):</b>   <code>{st['put_1']} PE</code>  |  <code>{st['put_2']} PE</code>\n")

    out.append("🔵 <b>CPR / PIVOT RANGE</b>")
    out.append("<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>")
    out.append(f"  TC (Top Pivot)    : <b>{lvl['tc']}</b>")
    out.append(f"  P  (Central Pivot): <b>{lvl['pivot']}</b>")
    out.append(f"  BC (Bottom Pivot) : <b>{lvl['bc']}</b>")
    out.append(f"  Expected Range    : <b>{lvl['range']} pts</b>\n")

    out.append("🟢 <b>BUYING ZONE (BULLISH ENTRY)</b>")
    out.append("<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>")
    out.append(f"  Buy Above         : <b>{lvl['b_above']}</b>")
    out.append(f"  Buy Confirm       : <b>{lvl['b_conf']}</b> <i>(Safe Entry)</i>")
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'bt{i}']}</b>")
    out.append("")

    out.append("🟠 <b>NO TRADE ZONE (RANGEBOUND)</b>")
    out.append("<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>")
    out.append(f"  Zone Range        : <b>{lvl['s_conf']}  ⇄  {lvl['b_conf']}</b>")
    out.append("  <i>(Strictly avoid taking fresh intraday entries inside this zone)</i>\n")

    out.append("🔴 <b>SELLING ZONE (BEARISH ENTRY)</b>")
    out.append("<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>")
    out.append(f"  Sell Below        : <b>{lvl['s_below']}</b>")
    out.append(f"  Sell Confirm      : <b>{lvl['s_conf']}</b> <i>(Safe Entry)</i>")
    for i in range(1, 11):
        out.append(f"  Target {i:<2}         : <b>{lvl[f'st{i}']}</b>")

    out.append(SEBI_DISCLAIMER_TEXT)
    return "\n".join(out)

# Automated Background Loop: Pre-Open Pulse (9:08 AM) & Levels (9:30 AM)
async def auto_post_scheduler_loop(bot):
    ist = pytz.timezone('Asia/Kolkata')
    last_levels_day = None
    last_preopen_day = None

    while True:
        try:
            now_ist = datetime.now(ist)
            today_date_str = now_ist.strftime('%Y-%m-%d')
            today_str = now_ist.strftime('%Y-%m-%d %I:%M %p IST')
            is_weekend = now_ist.weekday() >= 5
            is_nse_holiday = (today_date_str in NSE_HOLIDAYS) or is_weekend

            # 1. Pre-Open Pulse at 9:08 AM IST
            if now_ist.hour == 9 and now_ist.minute == 8 and last_preopen_day != today_date_str:
                last_preopen_day = today_date_str
                if not is_nse_holiday:
                    pulse_msg = (
                        "🌅 <b>PRE-OPEN MARKET PULSE (9:08 AM IST)</b>\n"
                        f"📅 <code>{today_str}</code>\n"
                        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
                        "⚡ <b>Session Opening Status:</b> Pre-Open Settled\n"
                        "📈 Intraday algorithmic OP levels will unlock at <b>9:30 AM IST</b>.\n"
                        "🔔 Keep @LIVEOPTIONDATA notifications active!"
                        + SEBI_DISCLAIMER_TEXT
                    )
                    await bot.send_message(chat_id=CHANNEL_USERNAME, text=pulse_msg, parse_mode="HTML")

            # 2. Daily OP Levels at 9:30 AM IST
            if now_ist.hour == 9 and now_ist.minute == 30 and last_levels_day != today_date_str:
                last_levels_day = today_date_str
                symbols_to_post = []
                if not is_nse_holiday:
                    symbols_to_post.extend(["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"])
                symbols_to_post.extend(["BTC", "ETH"])

                for sym in symbols_to_post:
                    try:
                        df, matched_symbol = fetch_data(sym)
                        if df is not None:
                            lvl = calculate_op_levels(df, sym)
                            msg_content = (
                                f"🔔 <b>MORNING INTRADAY AUTO-LEVELS (9:30 AM)</b>\n\n"
                                + format_color_display(matched_symbol, today_str, lvl)
                            )
                            await bot.send_message(chat_id=CHANNEL_USERNAME, text=msg_content, parse_mode="HTML")
                            await asyncio.sleep(2)
                    except Exception as e:
                        logging.error(f"Error auto-posting {sym}: {e}")

            await asyncio.sleep(30)
        except Exception as e:
            logging.error(f"Error in scheduler loop: {e}")
            await asyncio.sleep(30)

async def send_how_to_use(update: Update):
    guide_text = (
        "📖 <b>Universal OP Bot ka उपयोग कैसे करें? / User Guide:</b>\n\n"
        "<b>1️⃣ स्टेप 1 (सिम्बल चुनें):</b>\n"
        "नीचे दिए गए कीबोर्ड से इंडेक्स चुनें या किसी भी शेयर का नाम लिखें।\n\n"
        "<b>2️⃣ स्टेप 2 (2% स्ट्राइक सेलेक्टर):</b>\n"
        "बॉट स्पॉट प्राइस का ±2% कैलकुलेट करके सबसे सुरक्षित CE और PE स्ट्राइक अपने आप बताएगा।\n\n"
        "<b>3️⃣ स्टेप 3 (VIP फास्ट पास):</b>\n"
        "अपने 3 दोस्तों को बॉट रेफर करें और 30s टाइमर हमेशा के लिए स्किप करें! (`👥 VIP Referral Link` दबाएँ)।\n\n"
        "<b>4️⃣ स्टेप 4 (रिस्क कैलकुलेटर):</b>\n"
        "लॉट साइज कैलकुलेट करने के लिए `🧮 Lot Size Calculator` बटन दबाएँ।\n"
        + SEBI_DISCLAIMER_TEXT
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Visit Official Channel", url=CHANNEL_LINK)]])
    if update.message:
        await update.message.reply_text(guide_text, parse_mode="HTML", reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(guide_text, parse_mode="HTML", reply_markup=markup)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)

    # Referral detection (e.g., /start ref_123456)
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].replace("ref_", ""))
            record_referral(referrer_id, user_id)
        except Exception:
            pass

    subscribed = await is_user_subscribed(context.bot, user_id)
    if not subscribed:
        msg = (
            "⚠️ <b>चैनल जॉइन करना अनिवार्य है! / Channel Subscription Required!</b>\n\n"
            "बॉट का उपयोग करने के लिए कृपया हमारे चैनल को जॉइन करें।\n"
            + SEBI_DISCLAIMER_TEXT
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_force_sub_markup())
        return

    vip_badge = "👑 <b>VIP Status: ACTIVE (Instant Access)</b>\n" if is_vip_user(user_id) else ""
    msg = (
        f"👋 <b>Universal OP Bot Pro में आपका स्वागत है!</b>\n\n"
        f"{vip_badge}"
        "लेवल्स देखने के लिए नीचे दिए गए कीबोर्ड से सिम्बल चुनें या नाम टाइप करके भेजें:\n"
        + SEBI_DISCLAIMER_TEXT
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_me = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user_id}"

    refs = load_data(REFERRALS_FILE, {})
    user_invites = len(refs.get(str(user_id), []))
    status_str = "✅ <b>VIP Status Unlocked! (No 30s Wait Timer)</b>" if user_invites >= 3 else f"⏳ <b>{3 - user_invites} और दोस्तों को जोड़ें VIP बनने के लिए!</b>"

    msg = (
        "👥 <b>UNIVERSAL OP BOT • VIP REFERRAL SYSTEM</b>\n\n"
        f"🔗 <b>आपका पर्सनल इनवाइट लिंक:</b>\n<code>{ref_link}</code>\n\n"
        f"📊 <b>आपके कुल रेफर्ड यूज़र्स:</b> <code>{user_invites} / 3</code>\n"
        f"⚡ <b>Status:</b> {status_str}\n\n"
        "💡 <i>अपने 3 दोस्तों को बॉट से जोड़ें और 30-सेकंड का वेट टाइमर हमेशा के लिए बंद करें!</i>"
        + SEBI_DISCLAIMER_TEXT
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Share With Friends (शेयर करें)", url=f"https://t.me/share/url?url={ref_link}&text=Get%20Free%20Intraday%20OP%20Levels%20%26%202%25%20Strikes%20Daily!")]])
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)

async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🧮 <b>INTRADAY LOT SIZE & RISK CALCULATOR</b>\n\n"
        "उपयोग का तरीका:\n"
        "<code>/calc &lt;Total Capital&gt; &lt;Risk%&gt;</code>\n\n"
        "उदाहरण:\n"
        "<code>/calc 50000 2</code>  <i>(₹50,000 कैपिटल और 2% रिस्क)</i>"
        + SEBI_DISCLAIMER_TEXT
    )
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(help_text, parse_mode="HTML")
        return

    try:
        cap = float(context.args[0])
        risk_pct = float(context.args[1])
        risk_amt = round(cap * (risk_pct / 100.0), 2)

        # Standard sl reference: Nifty 20 pts sl, Bank Nifty 40 pts sl
        nifty_lot_size = 25
        nifty_risk_per_lot = 20 * nifty_lot_size
        nifty_lots = max(1, int(risk_amt / nifty_risk_per_lot))

        bn_lot_size = 15
        bn_risk_per_lot = 40 * bn_lot_size
        bn_lots = max(1, int(risk_amt / bn_risk_per_lot))

        res_msg = (
            f"🧮 <b>RISK MANAGEMENT ANALYSIS</b>\n\n"
            f"💵 <b>Capital:</b> ₹{cap:,.2f}\n"
            f"⚠️ <b>Max Risk ({risk_pct}%):</b> ₹{risk_amt:,.2f}\n\n"
            f"📊 <b>NIFTY 50 (20 pts SL assumed):</b>\n"
            f"  • Safe Quantity: <b>{nifty_lots * nifty_lot_size} qty</b> ({nifty_lots} lots)\n\n"
            f"📈 <b>BANK NIFTY (40 pts SL assumed):</b>\n"
            f"  • Safe Quantity: <b>{bn_lots * bn_lot_size} qty</b> ({bn_lots} lots)\n\n"
            f"<i>Never risk more than {risk_pct}% on a single trading day!</i>"
            + SEBI_DISCLAIMER_TEXT
        )
        await update.message.reply_text(res_msg, parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ कृपया सही नंबर डालें। उदाहरण: <code>/calc 50000 2</code>", parse_mode="HTML")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_data(USERS_FILE, [])
    refs = load_data(REFERRALS_FILE, {})
    await update.message.reply_text(
        f"📊 <b>Bot Admin Analytics:</b>\n\n"
        f"👥 कुल एक्टिव यूज़र्स: <b>{len(users)}</b>\n"
        f"🔗 रेफरल डेटाबेस साइज: <b>{len(refs)} referrers</b>\n"
        f"📢 ऑफिशियल चैनल: @LIVEOPTIONDATA", 
        parse_mode="HTML"
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("⚠️ उपयोग: <code>/broadcast आपका संदेश</code>", parse_mode="HTML")
        return

    text_to_broadcast = " ".join(context.args)
    final_broadcast_msg = text_to_broadcast + SEBI_DISCLAIMER_TEXT
    users = load_data(USERS_FILE, [])
    sent_count = 0
    failed_count = 0

    status_msg = await update.message.reply_text(f"⏳ ब्रॉडकास्ट भेजा जा रहा है... ({len(users)} यूज़र्स को)")
    channel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Open Channel (@LIVEOPTIONDATA)", url=CHANNEL_LINK)]])

    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=final_broadcast_msg, parse_mode="HTML", reply_markup=channel_markup)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed_count += 1
            continue

    await status_msg.edit_text(
        f"✅ <b>ब्रॉडकास्ट पूरा हुआ!</b>\n\n"
        f"✔️ भेजा गया: <b>{sent_count}</b> यूज़र्स को\n"
        f"❌ असफल: <b>{failed_count}</b>\n"
        f"👥 कुल डेटाबेस: <b>{len(users)}</b>", 
        parse_mode="HTML"
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    save_user(user_id)
    data = query.data

    if data == "check_joined":
        await query.answer()
        subscribed = await is_user_subscribed(context.bot, user_id)
        if subscribed:
            await query.message.delete()
            await query.message.reply_text(
                "✅ <b>वेरिफिकेशन सफल!</b>\n\nअब नीचे दिए गए कीबोर्ड से सिम्बल चुनें।" + SEBI_DISCLAIMER_TEXT, 
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )
        else:
            await query.message.reply_text(
                "❌ <b>आपने अभी तक चैनल जॉइन नहीं किया है!</b>" + SEBI_DISCLAIMER_TEXT, 
                reply_markup=get_force_sub_markup(),
                parse_mode="HTML"
            )

    elif data.startswith("lvl_"):
        symbol = data.replace("lvl_", "").strip()

        subscribed = await is_user_subscribed(context.bot, user_id)
        if not subscribed:
            await query.answer("⚠️ कृपया पहले चैनल जॉइन करें!", show_alert=True)
            await query.message.reply_text("🔒 चैनल जॉइन करें:" + SEBI_DISCLAIMER_TEXT, reply_markup=get_force_sub_markup(), parse_mode="HTML")
            return

        await query.answer()
        await query.message.delete()
        
        status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ <b>{symbol}</b> के OP लेवल्स निकाले जा रहे हैं...", parse_mode="HTML")
        
        try:
            df, matched_symbol = fetch_data(symbol)
            if df is None:
                await status_msg.edit_text(f"❌ <b>'{symbol}'</b> का डेटा नहीं मिला।" + SEBI_DISCLAIMER_TEXT, parse_mode="HTML")
                return

            lvl = calculate_op_levels(df, symbol)
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
            formatted_msg = format_color_display(matched_symbol, today_str, lvl)
            
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]])
            await status_msg.delete()
            await context.bot.send_message(chat_id=user_id, text=formatted_msg, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logging.error(f"Error delivering level: {e}")
            await status_msg.edit_text("❌ लेवल्स निकालने में समस्या आई।" + SEBI_DISCLAIMER_TEXT)

def make_big_timer_box(sec_left):
    total = 30
    filled = int(((total - sec_left) / total) * 10)
    bar = "■" * filled + "□" * (10 - filled)
    
    return (
        f"╔══════════════════════════════════════╗\n"
        f"║     ⏳ SPONSOR AD VERIFICATION       ║\n"
        f"╠══════════════════════════════════════╣\n"
        f"║                                      ║\n"
        f"║   ⏱️ TIME REMAINING :  [ {sec_left:02d}s ]       ║\n"
        f"║                                      ║\n"
        f"║   📊 PROGRESS : [{bar}]     ║\n"
        f"║   ⚡ STATUS   : VERIFYING VISIT...    ║\n"
        f"║                                      ║\n"
        f"╚══════════════════════════════════════╝"
    )

async def live_countdown_unlock(bot, chat_id, message_id, query_symbol):
    for sec_left in range(27, -1, -3):
        await asyncio.sleep(3)
        if sec_left > 0:
            box = make_big_timer_box(sec_left)
            msg_text = (
                f"📊 <b>{query_symbol} OP LEVELES • VERIFICATION IN PROGRESS</b>\n\n"
                f"<pre>{box}</pre>\n"
                f"👉 <b>निर्देश:</b> नीचे दिए गए बटन से चैनल पर जाएँ और स्पॉन्सर पोस्ट देखें।\n"
                f"<i>Stay on the channel until countdown reaches 00s.</i>"
                + SEBI_DISCLAIMER_TEXT
            )
            btn_label = f"👉 1. Open Channel & View Ad ({sec_left:02d}s)"
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, url=CHANNEL_LINK)]])
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=msg_text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                pass

    unlocked_box = (
        f"╔══════════════════════════════════════╗\n"
        f"║     ✅ VERIFICATION SUCCESSFUL!      ║\n"
        f"╠══════════════════════════════════════╣\n"
        f"║                                      ║\n"
        f"║   🎉 TIME COMPLETE : [ 00s ]         ║\n"
        f"║   📊 PROGRESS      : [■■■■■■■■■■]    ║\n"
        f"║   🚀 STATUS        : ACCESS GRANTED  ║\n"
        f"║                                      ║\n"
        f"╚══════════════════════════════════════╝"
    )
    final_text = (
        f"🎉 <b>{query_symbol} • LEVELS UNLOCKED!</b>\n\n"
        f"<pre>{unlocked_box}</pre>\n"
        f"सत्यापन पूरा हुआ! नीचे दिए गए <b>'GET LEVELS NOW'</b> बटन पर क्लिक करें:"
        + SEBI_DISCLAIMER_TEXT
    )
    unlocked_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Channel (@LIVEOPTIONDATA)", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🚀 2. GET LEVELS NOW (Unlocked 🎉)", callback_data=f"lvl_{query_symbol}")]
    ])
    
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text, parse_mode="HTML", reply_markup=unlocked_markup)
    except Exception as e:
        logging.error(f"Error updating message after delay: {e}")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    save_user(user_id)
    raw_text = update.message.text.strip()

    if raw_text.startswith("/"):
        return

    if "HOW TO USE" in raw_text.upper() or "गाइड" in raw_text:
        await send_how_to_use(update)
        return

    if "VIP REFERRAL LINK" in raw_text.upper():
        await referral_command(update, context)
        return

    if "LOT SIZE CALCULATOR" in raw_text.upper():
        await calc_command(update, context)
        return

    if "OFFICIAL CHANNEL" in raw_text.upper():
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Open Channel (@LIVEOPTIONDATA)", url=CHANNEL_LINK)]])
        await update.message.reply_text("📢 हमारे ऑफिशियल चैनल से जुड़ें:" + SEBI_DISCLAIMER_TEXT, reply_markup=markup, parse_mode="HTML")
        return

    clean_symbol = raw_text.replace("📊", "").replace("📈", "").replace("🪙", "").replace("⚡", "").replace("🥇", "").replace("🛢️", "").strip().upper()

    subscribed = await is_user_subscribed(context.bot, user_id)
    if not subscribed:
        await update.message.reply_text(
            "🔒 लेवल्स देखने के लिए पहले चैनल <b>@LIVEOPTIONDATA</b> जॉइन करें।" + SEBI_DISCLAIMER_TEXT, 
            parse_mode="HTML", 
            reply_markup=get_force_sub_markup()
        )
        return

    # Check for VIP status: VIP bypasses 30s timer directly
    if is_vip_user(user_id):
        status_msg = await update.message.reply_text(f"⚡ <b>VIP Instant Access:</b> {clean_symbol} के लेवल्स लोड हो रहे हैं...", parse_mode="HTML")
        df, matched_symbol = fetch_data(clean_symbol)
        if df is not None:
            lvl = calculate_op_levels(df, clean_symbol)
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
            formatted_msg = format_color_display(matched_symbol, today_str, lvl)
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]])
            await status_msg.delete()
            await update.message.reply_text(formatted_msg, parse_mode="HTML", reply_markup=markup)
            return

    # Normal user gets the 30s visit timer
    start_box = make_big_timer_box(30)
    initial_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👉 1. Open Channel & View Ad (30s)", url=CHANNEL_LINK)]
    ])

    sent_msg = await update.message.reply_text(
        f"📊 <b>{clean_symbol} OP LEVELES • VERIFICATION IN PROGRESS</b>\n\n"
        f"<pre>{start_box}</pre>\n"
        f"👉 <b>निर्देश:</b> नीचे दिए गए बटन से चैनल पर जाएँ और स्पॉन्सर पोस्ट देखें।\n"
        f"<i>Stay on the channel until countdown reaches 00s.</i>"
        + SEBI_DISCLAIMER_TEXT,
        parse_mode="HTML",
        reply_markup=initial_markup
    )

    asyncio.create_task(live_countdown_unlock(context.bot, sent_msg.chat_id, sent_msg.message_id, clean_symbol))

async def post_init(application):
    asyncio.create_task(auto_post_scheduler_loop(application.bot))

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ref", referral_command))
    app.add_handler(CommandHandler("calc", calc_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    
    print("Bot is polling live with Pro Suite (Strikes, Pre-Open, VIP Referral, Calc)...")
    app.run_polling()

if __name__ == "__main__":
    main()
