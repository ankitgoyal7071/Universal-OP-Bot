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
    "<i>We are NOT SEBI Registered advisors/analysts. All levels & data provided are purely algorithmic calculations for EDUCATIONAL & PAPER TRADING purposes only. Options & Futures trading involves high risk. We are not responsible for any financial loss. Please consult your SEBI registered financial advisor before trading.</i>\n"
    "🔒 <b>Official Channel:</b> @LIVEOPTIONDATA"
)

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_user(user_id):
    users = load_users()
    if user_id not in users:
        users.add(user_id)
        try:
            with open(USERS_FILE, "w") as f:
                json.dump(list(users), f)
        except Exception as e:
            logging.error(f"Error saving user: {e}")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive and healthy!")

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
        [KeyboardButton("🪙 FIN NIFTY"), KeyboardButton("⚡ MIDCAP NIFTY")],
        [KeyboardButton("🥇 MCX GOLD"), KeyboardButton("🛢️ CRUDE OIL")],
        [KeyboardButton("📖 How to Use? (गाइड)"), KeyboardButton("📢 Official Channel")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_force_sub_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel / चैनल जॉइन करें", url=CHANNEL_LINK)],
        [InlineKeyboardButton("🔄 Verify Joined / वेरिफाई करें", callback_data="check_joined")]
    ])

def calculate_op_levels(df):
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

    return {
        "range": range1, "pivot": pivot, "tc": tc, "bc": bc,
        "cpr_type": cpr_type,
        "b_above": round(doKdc + (range1 * 0.236)),
        "b_conf": round(doKdc + (range1 * 0.382)),
        "s_below": round(doKdc - (range1 * 0.236)),
        "s_conf": round(doKdc - (range1 * 0.382)),
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
    out = []
    out.append(f"📊 <b>{matched_symbol} • OP CALCULATIONS</b>")
    out.append(f"📅 <code>{today_str}</code>")
    out.append(f"🎯 <b>CPR Setup:</b> <code>{lvl['cpr_type']}</code>\n")

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

async def auto_post_scheduler_loop(bot):
    ist = pytz.timezone('Asia/Kolkata')
    last_posted_day = None

    while True:
        try:
            now_ist = datetime.now(ist)
            today_date_str = now_ist.strftime('%Y-%m-%d')
            
            if now_ist.hour == 9 and now_ist.minute == 30 and last_posted_day != today_date_str:
                last_posted_day = today_date_str
                is_weekend = now_ist.weekday() >= 5
                is_nse_holiday = (today_date_str in NSE_HOLIDAYS) or is_weekend
                today_str = now_ist.strftime('%Y-%m-%d %I:%M %p IST')

                symbols_to_post = []
                if not is_nse_holiday:
                    symbols_to_post.extend(["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"])
                symbols_to_post.extend(["BTC", "ETH"])

                for sym in symbols_to_post:
                    try:
                        df, matched_symbol = fetch_data(sym)
                        if df is not None:
                            lvl = calculate_op_levels(df)
                            msg_content = (
                                f"🔔 <b>MORNING INTRADAY AUTO-LEVELS (9:30 AM)</b>\n\n"
                                + format_color_display(matched_symbol, today_str, lvl)
                            )
                            await bot.send_message(
                                chat_id=CHANNEL_USERNAME,
                                text=msg_content,
                                parse_mode="HTML"
                            )
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
        "नीचे दिए गए कीबोर्ड से इंडेक्स चुनें (उदा. NIFTY 50) या किसी भी शेयर का नाम लिखें।\n\n"
        "<b>2️⃣ स्टेप 2 (चैनल पर जाएँ):</b>\n"
        "<b>'👉 1. Open Channel & View Ad'</b> बटन दबाकर चैनल <b>@LIVEOPTIONDATA</b> पर जाएँ और लेटेस्ट स्पॉन्सर पोस्ट देखें।\n\n"
        "<b>3️⃣ स्टेप 3 (लाइव बिग काउंटडाउन):</b>\n"
        "स्क्रीन पर बड़ा डिजिटल टाइमर बॉक्स चलेगा (30s से 0s तक)।\n\n"
        "<b>4️⃣ स्टेप 4 (लेवल्स अनलॉक):</b>\n"
        "समय पूरा होते ही <b>'🚀 2. GET LEVELS NOW (Unlocked 🎉)'</b> बटन आएगा। उस पर क्लिक करके तुरंत लेवल्स देखें!\n"
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
    subscribed = await is_user_subscribed(context.bot, user_id)
    
    if not subscribed:
        msg = (
            "⚠️ <b>चैनल जॉइन करना अनिवार्य है! / Channel Subscription Required!</b>\n\n"
            "बॉट का उपयोग करने के लिए कृपया हमारे चैनल को जॉइन करें।\n"
            "<i>Please join our official channel to use this bot.</i>"
            + SEBI_DISCLAIMER_TEXT
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_force_sub_markup())
        return

    msg = (
        "👋 <b>Universal OP Bot में आपका स्वागत है! / Welcome!</b>\n\n"
        "लेवल्स देखने के लिए नीचे दिए गए कीबोर्ड से सिम्बल चुनें या नाम टाइप करके भेजें:\n"
        "<i>Select any symbol from the quick menu below:</i>"
        + SEBI_DISCLAIMER_TEXT
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_keyboard())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    await update.message.reply_text(
        f"📊 <b>Bot Admin Analytics:</b>\n\n"
        f"👥 कुल एक्टिव यूज़र्स (Total Users): <b>{len(users)}</b>\n"
        f"📢 ऑफिशियल चैनल: @LIVEOPTIONDATA", 
        parse_mode="HTML"
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>उपयोग का तरीका (Usage):</b>\n"
            "<code>/broadcast आपका प्रचार संदेश यहाँ लिखें</code>", 
            parse_mode="HTML"
        )
        return

    text_to_broadcast = " ".join(context.args)
    final_broadcast_msg = text_to_broadcast + SEBI_DISCLAIMER_TEXT
    users = load_users()
    sent_count = 0
    failed_count = 0

    status_msg = await update.message.reply_text(f"⏳ ब्रॉडकास्ट भेजा जा रहा है... (कुल {len(users)} यूज़र्स को)")
    channel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Open Channel (@LIVEOPTIONDATA)", url=CHANNEL_LINK)]])

    for uid in users:
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=final_broadcast_msg, 
                parse_mode="HTML",
                reply_markup=channel_markup
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed_count += 1
            continue

    await status_msg.edit_text(
        f"✅ <b>ब्रॉडकास्ट सफलतापूर्वक पूरा हुआ!</b>\n\n"
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
                "✅ <b>वेरिफिकेशन सफल! / Verification Successful!</b>\n\n"
                "अब नीचे दिए गए कीबोर्ड से सिम्बल चुनें।" + SEBI_DISCLAIMER_TEXT, 
                parse_mode="HTML",
                reply_markup=get_main_keyboard()
            )
        else:
            await query.message.reply_text(
                "❌ <b>आपने अभी तक चैनल जॉइन नहीं किया है!</b>\n"
                "<i>You haven't joined the channel yet! Please join first:</i>" + SEBI_DISCLAIMER_TEXT, 
                reply_markup=get_force_sub_markup(),
                parse_mode="HTML"
            )

    elif data.startswith("lvl_"):
        symbol = data.replace("lvl_", "").strip()

        subscribed = await is_user_subscribed(context.bot, user_id)
        if not subscribed:
            await query.answer("⚠️ कृपया पहले चैनल जॉइन करें!", show_alert=True)
            await query.message.reply_text("🔒 बॉट का उपयोग करने के लिए चैनल जॉइन करें:" + SEBI_DISCLAIMER_TEXT, reply_markup=get_force_sub_markup(), parse_mode="HTML")
            return

        await query.answer()
        await query.message.delete()
        
        status_msg = await context.bot.send_message(
            chat_id=user_id, 
            text=f"⏳ <b>{symbol}</b> के OP लेवल्स निकाले जा रहे हैं...\n<i>Calculating OP levels...</i>", 
            parse_mode="HTML"
        )
        
        try:
            df, matched_symbol = fetch_data(symbol)
            if df is None:
                await status_msg.edit_text(
                    f"❌ <b>'{symbol}'</b> का डेटा नहीं मिला / Data Not Found.\n\n"
                    "कृपया सही सिम्बल चुनें या नाम लिखें।" + SEBI_DISCLAIMER_TEXT,
                    parse_mode="HTML"
                )
                return

            lvl = calculate_op_levels(df)
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d %I:%M %p IST')
            formatted_msg = format_color_display(matched_symbol, today_str, lvl)
            
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK)]])
            await status_msg.delete()
            await context.bot.send_message(chat_id=user_id, text=formatted_msg, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logging.error(f"Error delivering level: {e}")
            await status_msg.edit_text("❌ लेवल्स निकालने में समस्या आई। / Error calculating levels." + SEBI_DISCLAIMER_TEXT)

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
            markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"👉 1. Open Channel & View Ad ({sec
